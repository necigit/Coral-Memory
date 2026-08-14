# -*- coding: utf-8 -*-
"""
ThreeDogCoral（脑珊瑚 / Brain Coral）—— 动态、可生长的记忆缓存系统
=====================================================

作者：Mr. Code Muggle (@Ne) <751286928@qq.com>
（如果你基于本项目二次开发，请在代码/README 中保留作者痕迹，
    并欢迎到仓库 issue/PR 交流 —— 看看能长出多少棵珊瑚。）

从 ThreeDogMemory 平滑升级而来，保留原有三级存储骨架与内部方法命名：

    _evict_check / _dump_cold / _trim_cold_storage / _search_cold / _save_warm ...

升级点：

1. 存储升级
   - 保留热 / 温 / 冷三级文本存储；
   - 每条记忆额外生成语义向量（默认 sentence-transformers/all-MiniLM-L6-v2 384 维，
     可配置为 BAAI/bge-small-zh-v1.5 512 维，见 coral_config.json），
     向量独立存为 .npy 文件（+ 一个 id 索引 JSON），与记忆数据并行维护；
   - 若环境未安装 sentence-transformers，自动降级为确定性的哈希嵌入（可配置）。

2. 检索升级
   - 多路融合检索：向量余弦相似度(0.6) + 关键词 Jaccard(0.2) + 时间衰减(0.2)；
   - 时间衰减：exp(-ΔT / τ)，τ 默认 7 天；
   - 返回 Top-K（默认 5）结果，每条附带综合得分与分项得分。

3. 淘汰升级
   - 动态热度分：H = 访问频率(0.4) + 最近访问时间(0.3) + 用户显式重要性(0.3)；
   - 记忆总数超过容量阈值（默认 1000）时：先尝试蒸馏（LLM 压缩相似记忆为摘要，
     _distill 为占位接口），再淘汰热度最低的记忆。

4. Harness 工具注册
   - @register_tool 装饰器注册 memory_search / memory_insert；
   - 可导出为 DSH cordis 插件的 JS 定义（build_dsh_cordis_plugin_js），
     并提供 MemoryToolSidecar 把 JS 调用桥接回 Python 注册表。

5. 配置热加载
   - 所有阈值（相似度、TTL、容量、权重、τ）从 coral_config.json 读取，
     reload_config() / 自动 mtime 检测 支持运行时重载，无需重启服务。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional

import numpy as np

logger = logging.getLogger("three_dog_coral")

__author__ = "Mr. Code Muggle (@Ne)"
__email__ = "751286928@qq.com"
__version__ = "0.1.0"
__all__ = [
    "ThreeDogCoral",
    "MemoryItem",
    "SearchHit",
    "register_tool",
    "TOOL_REGISTRY",
    "memory_search",
    "memory_insert",
    "get_coral",
    "export_dsh_tool_definitions",
    "build_dsh_cordis_plugin_js",
    "MemoryToolSidecar",
    "load_config",
]

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "coral_config.json")

# ---------------------------------------------------------------------------
# 默认配置（与 coral_config.json 保持一致；加载时做深合并，允许部分覆盖）
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: Dict[str, Any] = {
    "paths": {
        "warm_cache": "memory_data/coral_warm.json",
        "cold_archive": "memory_data/coral_cold.jsonl",
        "vector_store": "memory_data/coral_vectors.npy",
        "vector_index": "memory_data/coral_vector_index.json",
    },
    "embedding": {
        "embedder": "sentence-transformers",  # "sentence-transformers" | "hash"
        "model_name": "sentence-transformers/all-MiniLM-L6-v2",
        "dim": 384,
        "normalize": True,
    },
    "memory": {
        "sim_threshold_hot": 0.7,
        "sim_threshold_warm": 0.55,
        "sim_threshold_cold": 0.45,
        "hot_ttl_hours": 24,
        "max_hot_entries": 50,
        "max_warm_entries": 200,
        "max_cold_entries": 1000,
        "token_fuse_threshold": 4000,
        "capacity_threshold": 1000,      # 记忆总数（热+温+冷）超此值触发蒸馏/淘汰
        "governance_headroom": 0,        # 治理余量：0 = 自动 max(10, min(容量/10, 200))，避免超容后每次 insert 都全量治理
        "distill_first": True,           # 先蒸馏再淘汰
        "distill_sim_threshold": 0.6,    # 聚类蒸馏的相似度阈值
        "distill_min_cluster": 3,        # 最少多少条相似记忆才值得蒸馏
        "cold_scan_lines": 500,          # 冷库检索时最多扫描多少行（尾部最新）
    },
    "retrieval": {
        "weights": {"vector": 0.6, "jaccard": 0.2, "time": 0.2},
        "top_k": 5,
        "tau_days": 7.0,                 # 时间衰减 τ
        "min_score": 0.0,                # 综合得分下限（0 表示不过滤）
        "include_cold": True,            # 检索是否总是扫描冷库
    },
    "heat": {
        "weights": {"frequency": 0.4, "recency": 0.3, "importance": 0.3},
        "tau_days": 7.0,                 # 热度里"最近访问"的时间常数
        "freq_scale": 10.0,              # 频率对数刻度分母：freq = log2(1+c)/log2(1+scale)
        "cold_fold_interval_seconds": 30,  # 冷库热度增量落盘的节流间隔
    },
    "reload": {"check_interval_seconds": 2.0},
    # 并发/并行优化（2020 年前后消费级 CPU：6C/12T ~ 8C/16T）
    "parallelism": {
        # 并发嵌入请求的合并窗口。0 = 关闭（hash 嵌入/调试用，避免无谓等待）；
        # 真实 sentence-transformers 模型建议 4~8ms：单条 CPU 编码 ~10-30ms，
        # 合批后 8 路并发工具调用只需一次批量编码。
        "embed_batch_window_ms": 0,
        "vectorized_jaccard": True,   # 用 numpy 位图向量化 Jaccard（绕开 GIL）
    },
    # 存储/磁盘
    "storage": {
        "vector_save_interval_seconds": 5.0,  # 向量落盘节流（flush 时恒落盘）；防 2 万量级 O(n²) 写盘
        "max_bytes": 0,                       # 磁盘配额硬线（字节），0 = 不限制
        "warn_ratio": 0.8,                    # 占用超过 硬线×此比例 时告警
        "hard_ratio": 0.85,                   # 超硬线后按热度淘汰冷库，回落到 硬线×此比例
    },
}


# ---------------------------------------------------------------------------
# 配置加载 / 深合并 / 热重载
# ---------------------------------------------------------------------------
def _recursive_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """深合并：override 中的 dict 递归合并，其余键直接覆盖。"""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _recursive_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """读取 coral_config.json，与默认值深合并；文件缺失/损坏时回退默认。"""
    path = path or DEFAULT_CONFIG_PATH
    cfg = _recursive_merge(DEFAULT_CONFIG, {})
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
            if isinstance(user_cfg, dict):
                cfg = _recursive_merge(cfg, user_cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("coral_config.json 解析失败（%s），使用默认配置", exc)
    else:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.warning("无法写入默认配置 %s：%s", path, exc)
    return cfg


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass
class MemoryItem:
    item_id: str
    content: str
    timestamp: float
    last_access: float
    token_count: int
    access_count: int = 0          # 访问频率（热度 0.4）
    importance: float = 0.0        # 用户显式标记的重要性 0~1（热度 0.3）


@dataclass
class SearchHit:
    """一条检索结果：附带综合得分与分项得分。"""

    item: MemoryItem
    score: float
    scores: Dict[str, float] = field(default_factory=dict)  # {"vector":..,"jaccard":..,"time":..}

    @property
    def content(self) -> str:
        return self.item.content

    @property
    def item_id(self) -> str:
        return self.item.item_id

    def __getattr__(self, name: str) -> Any:
        # 兼容旧代码：直接把 item 的字段透传出来
        return getattr(self.item, name)


# ---------------------------------------------------------------------------
# 向量存储（独立 .npy + id 索引，与记忆数据并行维护）
# ---------------------------------------------------------------------------
class _VectorStore:
    """矩阵存 .npy，id 列表存同名 .json，二者按行对齐。原子写入防半截文件。

    性能设计（修复 2 万量级 O(n²) 反噬）：
    - 几何扩容：追加不再每行 vstack 全量拷贝（amortized O(1)）；
    - 落盘只写有效行（内部有预分配余量）；
    - drop 原地压缩，不重建整矩阵。
    """

    _GROW_MIN = 1024

    def __init__(self, path_npy: str, path_index: str, dim: int):
        self.path_npy = path_npy
        self.path_index = path_index
        self.dim = dim
        self._matrix: np.ndarray = np.zeros((0, dim), dtype=np.float32)
        self._cap = 0      # 已分配行容量（几何增长）
        self._n = 0        # 有效行数（<= _cap）
        self._ids: List[str] = []
        self._pos: Dict[str, int] = {}
        self.dirty = False

    # ---------- 读写 ----------
    def load(self) -> None:
        try:
            if not (os.path.exists(self.path_npy) and os.path.exists(self.path_index)):
                return
            matrix = np.load(self.path_npy)
            with open(self.path_index, "r", encoding="utf-8") as f:
                ids = json.load(f)
            if not isinstance(ids, list):
                return
            if matrix.ndim != 2 or matrix.shape[1] != self.dim or len(ids) != matrix.shape[0]:
                logger.warning("向量存储形状与索引不一致，重建空存储：%s", self.path_npy)
                return
            self._matrix = np.asarray(matrix, dtype=np.float32)
            self._ids = [str(i) for i in ids]
            self._pos = {item_id: i for i, item_id in enumerate(self._ids)}
            self._n = len(self._ids)
            self._cap = self._n
            logger.info("向量存储加载完成：%d 条（%s）", self._n, self.path_npy)
        except Exception as exc:  # noqa: BLE001
            logger.warning("向量存储加载失败（%s），使用空存储", exc)

    def _ensure_capacity(self, need: int) -> None:
        """几何扩容：容量翻倍，amortized O(1)。"""
        if need <= self._cap:
            return
        new_cap = max(self._GROW_MIN, self._cap * 2)
        while new_cap < need:
            new_cap *= 2
        new_matrix = np.zeros((new_cap, self.dim), dtype=np.float32)
        if self._n:
            new_matrix[: self._n] = self._matrix[: self._n]
        self._matrix = new_matrix
        self._cap = new_cap

    def save(self) -> None:
        if not self.dirty:
            return
        # 注意：np.save 会自动追加 ".npy" 后缀，临时名因此要处理为 raw 前缀
        tmp_npy = self.path_npy + ".tmp"
        tmp_idx = self.path_index + ".tmp"
        np.save(tmp_npy, self._matrix[: self._n])     # 只写有效行
        with open(tmp_idx, "w", encoding="utf-8") as f:
            json.dump(self._ids[: self._n], f, ensure_ascii=False)
        os.replace(tmp_npy + ".npy", self.path_npy)
        os.replace(tmp_idx, self.path_index)
        self.dirty = False

    # ---------- 增删查 ----------
    def get(self, item_id: str) -> Optional[np.ndarray]:
        pos = self._pos.get(item_id)
        if pos is None:
            return None
        return self._matrix[pos]

    def upsert(self, item_id: str, vec: np.ndarray) -> None:
        v = np.asarray(vec, dtype=np.float32).reshape(-1)
        if v.shape[0] != self.dim:
            raise ValueError(f"向量维度 {v.shape[0]} != 配置维度 {self.dim}")
        pos = self._pos.get(item_id)
        if pos is not None:
            if not np.array_equal(self._matrix[pos], v):
                self._matrix[pos] = v
                self.dirty = True
            return
        self._ensure_capacity(self._n + 1)
        self._matrix[self._n] = v
        self._ids.append(item_id)
        self._pos[item_id] = self._n
        self._n += 1
        self.dirty = True

    def drop_many(self, item_ids: List[str]) -> None:
        """删除多条向量（原地压缩，冷库裁剪/淘汰时调用）。"""
        drop_set = set(item_ids)
        if not drop_set:
            return
        keep = 0
        for i in range(self._n):
            if self._ids[i] not in drop_set:
                if keep != i:
                    self._matrix[keep] = self._matrix[i]
                self._ids[keep] = self._ids[i]
                keep += 1
        del self._ids[keep:]
        if keep < self._n:
            self._matrix[keep:self._n] = 0  # 清尾防脏数据
        self._n = keep
        self._pos = {item_id: i for i, item_id in enumerate(self._ids)}
        self.dirty = True

    def __len__(self) -> int:
        return self._n


# ---------------------------------------------------------------------------
# 嵌入器：sentence-transformers 优先，缺失时降级为确定性哈希嵌入
# ---------------------------------------------------------------------------
_CJK = re.compile(r"[\u4e00-\u9fff]")


def _hash_tokens(text: str) -> List[str]:
    """分词：英文按词，中文按词 + 单字 unigram（保证无空格中文也有词面重合度）。"""
    lower = text.lower()
    words = re.findall(r"[a-z0-9\u4e00-\u9fff]+", lower)
    tokens: List[str] = []
    for w in words:
        tokens.append(w)
        if len(w) > 1 and _CJK.search(w):
            tokens.extend(list(w))  # 中文字符 unigram
    return tokens


def _jaccard_similarity(set_a: set, set_b: set) -> float:
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return inter / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# 位图 Jaccard（numpy 向量化，绕开 GIL；numpy>=2.0 用 np.bitwise_count）
# ---------------------------------------------------------------------------
_JACCARD_BITS = 2048
_JACCARD_WORDS = _JACCARD_BITS // 64
_HAS_BITCOUNT = hasattr(np, "bitwise_count")


def _token_bitmap(tokens: List[str]) -> np.ndarray:
    """把 token 列表映射成 (W,) uint64 位图（哈希到 2048 bit）。"""
    bits = np.zeros(_JACCARD_WORDS, dtype=np.uint64)
    for t in tokens:
        h = hashlib.blake2b(t.encode("utf-8"), digest_size=8).digest()
        bit = int.from_bytes(h, "little") % _JACCARD_BITS
        bits[bit >> 6] |= np.uint64(1) << (bit & 63)
    return bits


def _jaccard_many(query_bits: np.ndarray, item_bits: np.ndarray) -> np.ndarray:
    """一条查询位图 vs N 条条目位图，一次向量化算完 Jaccard。

    query_bits: (W,) uint64；item_bits: (N, W) uint64。
    """
    inter = np.bitwise_count(query_bits & item_bits).sum(axis=1)  # (N,)
    q_pop = int(np.bitwise_count(query_bits).sum())
    item_pop = np.bitwise_count(item_bits).sum(axis=1)            # (N,)
    union = q_pop + item_pop - inter
    out = np.zeros(len(item_bits), dtype=np.float64)
    np.divide(inter, union, out=out, where=union > 0)
    return out


class _HashEmbedder:
    """确定性哈希嵌入（hashing trick）：无外部依赖，维度与模型对齐，可离线运行。"""

    def __init__(self, dim: int):
        self.dim = dim

    def embed(self, texts: List[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in _hash_tokens(text):
                h = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
                i1 = int.from_bytes(h[0:4], "little") % self.dim
                i2 = int.from_bytes(h[4:8], "little") % self.dim
                sign = 1.0 if (h[8] & 1) else -1.0
                out[row, i1] += sign
                out[row, i2] += sign
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms


class _AsyncEmbedBatcher:
    """把窗口内并发的嵌入请求合并成一次模型 encode（coalescing）。

    消费级 CPU（6~8 核）上，模型批量编码的吞吐远高于逐条编码，
    并发工具调用（如 8 路并发 memory_search）在锁外排队，窗口内合批，
    是"异步数量提升"收益最大的环节。
    """

    def __init__(self, embed_fn: Callable[[List[str]], np.ndarray]):
        self._embed_fn = embed_fn
        self._pending: List[tuple] = []
        self._event: Optional[asyncio.Event] = None
        self._task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._window_ms = 0

    # ---------- 内部 ----------
    def _ensure(self, loop: asyncio.AbstractEventLoop, window_ms: int) -> None:
        if self._task is not None and self._loop is loop:
            return
        # 换 loop（主循环 <-> Sidecar 线程循环）时重建
        self._loop = loop
        self._window_ms = window_ms
        self._pending = []
        self._event = asyncio.Event()
        self._task = loop.create_task(self._run())

    async def _run(self) -> None:
        while True:
            await self._event.wait()
            self._event.clear()
            await asyncio.sleep(self._window_ms / 1000.0)  # 收集窗口
            batch, self._pending = self._pending, []
            if not batch:
                continue
            all_texts: List[str] = []
            for texts, _fut in batch:
                all_texts.extend(texts)
            try:
                vecs = self._embed_fn(all_texts)
                idx = 0
                for texts, fut in batch:
                    if not fut.done():
                        fut.set_result(vecs[idx: idx + len(texts)])
                    idx += len(texts)
            except Exception as exc:  # noqa: BLE001
                for _, fut in batch:
                    if not fut.done():
                        fut.set_exception(exc)

    # ---------- 对外 ----------
    async def embed_async(self, texts: List[str], window_ms: int) -> np.ndarray:
        if window_ms <= 0 or not texts:
            return self._embed_fn(texts)
        loop = asyncio.get_running_loop()
        self._ensure(loop, window_ms)
        self._window_ms = window_ms  # 窗口可运行时热改（读配置）
        fut = loop.create_future()
        self._pending.append((texts, fut))
        self._event.set()
        return await fut


class _Embedder:
    """懒加载封装：首次 embed 时才 import 模型；失败自动降级 hash。"""

    def __init__(self, cfg: Dict[str, Any]):
        self._cfg = dict(cfg)
        self._model: Any = None
        self._mode: str = "hash"
        self._resolved = False
        self._cache: Dict[str, np.ndarray] = {}

    def configure(self, cfg: Dict[str, Any]) -> bool:
        """配置变更（模型名/维度/模式）时置脏，下次 embed 重建。返回是否变化。"""
        if cfg == self._cfg:
            return False
        self._cfg = dict(cfg)
        self._model = None
        self._resolved = False
        self._cache.clear()
        return True

    # ---------- 内部 ----------
    def _resolve(self) -> None:
        if self._resolved:
            return
        self._resolved = True
        mode = self._cfg.get("embedder", "sentence-transformers")
        if mode == "sentence-transformers":
            try:
                from sentence_transformers import SentenceTransformer  # 本地模型
                name = self._cfg.get("model_name", "sentence-transformers/all-MiniLM-L6-v2")
                self._model = SentenceTransformer(name)
                self._mode = "sentence-transformers"
                logger.info("嵌入器就绪：%s（%d 维）", name, self._cfg.get("dim"))
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("sentence-transformers 不可用（%s），降级为确定性哈希嵌入", exc)
        self._mode = "hash"

    # ---------- 对外 ----------
    def embed(self, texts: List[str]) -> np.ndarray:
        self._resolve()
        if not texts:
            return np.zeros((0, self._cfg.get("dim", 384)), dtype=np.float32)
        if self._mode == "sentence-transformers":
            assert self._model is not None
            vecs = self._model.encode(
                texts,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            return np.asarray(vecs, dtype=np.float32)
        return _HashEmbedder(self._cfg.get("dim", 384)).embed(texts)

    def embed_one(self, text: str) -> np.ndarray:
        return self.embed([text])[0]


# ---------------------------------------------------------------------------
# Harness 工具注册：@register_tool
# ---------------------------------------------------------------------------
@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]
    fn: Callable[..., Any]


TOOL_REGISTRY: Dict[str, ToolSpec] = {}


def register_tool(
    name: Optional[str] = None,
    description: str = "",
    parameters: Optional[Dict[str, Any]] = None,
):
    """把函数注册进 TOOL_REGISTRY，供 Agent 直接调用。

    用法::

        @register_tool("memory_search", description="...", parameters={...})
        async def memory_search(query: str) -> dict: ...

    导出的工具可通过 build_dsh_cordis_plugin_js() 生成 DSH 的
    harness.registerTool(ctx, harness.defineTool({...})) 插件源码。
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        spec = ToolSpec(
            name=name or fn.__name__,
            description=description or (fn.__doc__ or "").strip().splitlines()[0],
            parameters=parameters or {},
            fn=fn,
        )
        TOOL_REGISTRY[spec.name] = spec
        return fn

    return decorator


# ---------------------------------------------------------------------------
# 脑珊瑚主类
# ---------------------------------------------------------------------------
class ThreeDogCoral:
    """动态、可生长的记忆缓存系统。

    - 三级存储：热（内存）/ 温（内存 + JSON）/ 冷（JSONL 落盘）
    - 向量存储：独立 .npy，与记忆数据并行维护
    - 检索：向量 0.6 + Jaccard 0.2 + 时间衰减 0.2，返回 Top-K 带分
    - 淘汰：热度分 H = 频率 0.4 + 最近访问 0.3 + 重要性 0.3；
      超容量时先蒸馏（占位接口）再淘汰最低热度
    """

    def __init__(self, config_path: Optional[str] = None, cfg: Optional[Dict[str, Any]] = None):
        self.config_path = config_path or DEFAULT_CONFIG_PATH
        self.cfg: Dict[str, Any] = cfg or load_config(self.config_path)
        self._cfg_mtime = self._stat_mtime()
        self._last_reload_check = 0.0

        paths = self.cfg["paths"]
        self.path_warm = paths["warm_cache"]
        self.path_cold = paths["cold_archive"]
        self.path_vectors = paths["vector_store"]
        self.path_vector_index = paths["vector_index"]

        os.makedirs(os.path.dirname(os.path.abspath(self.path_warm)), exist_ok=True)
        os.makedirs(os.path.dirname(os.path.abspath(self.path_cold)), exist_ok=True)

        # 内存区
        self.hot_memory: List[MemoryItem] = []
        self.warm_memory: List[MemoryItem] = []

        # 向量区（与记忆并行维护）
        self.vector_store = _VectorStore(
            self.path_vectors, self.path_vector_index, int(self.cfg["embedding"]["dim"])
        )
        self.vector_store.load()

        # 嵌入器（懒加载）
        self.embedder = _Embedder(self.cfg["embedding"])

        # 并发优化：嵌入合批 + 位图 Jaccard 缓存
        self._batcher = _AsyncEmbedBatcher(self.embedder.embed)
        self._bitmap_cache: Dict[str, np.ndarray] = {}

        # 冷库热度增量：检索命中暂存，节流/重写时合并回冷库 JSONL（修复"冷库热度不落盘"）
        self._cold_stat_bumps: Dict[str, int] = {}
        self._cold_last_access: Dict[str, float] = {}
        self._last_cold_fold = 0.0

        # 落盘节流 / 配额告警去重
        self._last_vec_save = 0.0
        self._quota_warned = False

        # 锁：按事件循环懒重建，兼容主循环与 Sidecar 线程各自的 loop
        self._lock = asyncio.Lock()
        self._lock_loop: Optional[asyncio.AbstractEventLoop] = None

        # 载入温存
        self._load_warm_sync()

        # 冷库条数缓存（避免每次 insert 都全量扫描冷文件；写路径负责维护）
        self._cold_count = self._count_cold()

        # 孤儿向量清理：热区不落盘，重启后其向量无主，只保留 温区+冷区 的向量
        self._prune_orphan_vectors()

    # ================= 工具函数 =================
    @staticmethod
    def _gen_id(content: str) -> str:
        return hashlib.md5(content.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _count_token(text: str) -> int:
        return int(len(text.encode("utf-8")) // 3.8)

    def _similarity_dummy(self, a: str, b: str) -> float:
        """关键词 Jaccard 相似度（沿用旧版命名）。"""
        return _jaccard_similarity(set(_hash_tokens(a)), set(_hash_tokens(b)))

    def _get_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def _collect_cold_ids_sync(self) -> set:
        """启动时同步读一遍冷库 id 集合（仅孤儿清理使用）。"""
        ids = set()
        if os.path.exists(self.path_cold):
            try:
                with open(self.path_cold, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            iid = json.loads(line).get("item_id")
                            if iid:
                                ids.add(iid)
                        except Exception:  # noqa: BLE001
                            continue
            except OSError:
                pass
        return ids

    def _prune_orphan_vectors(self) -> None:
        """清理无主向量（热区不落盘，重启后其向量成为孤儿，逐次累积）。"""
        warm_ids = {m.item_id for m in self.warm_memory}
        if len(self.vector_store) <= len(warm_ids) + self._cold_count:
            return
        cold_ids = self._collect_cold_ids_sync()
        alive = warm_ids | cold_ids
        orphans = [iid for iid in self.vector_store._ids if iid not in alive]
        if orphans:
            self.vector_store.drop_many(orphans)
            self.vector_store.save()
            logger.info("启动时清理 %d 条孤儿向量（热区不落盘）", len(orphans))

    # ================= 配置热加载 =================
    def _stat_mtime(self) -> float:
        try:
            return os.path.getmtime(self.config_path)
        except OSError:
            return 0.0

    def reload_config(self, force: bool = False) -> Dict[str, Any]:
        """热重载 coral_config.json：所有阈值（相似度/TTL/容量/权重/τ）即时生效。"""
        mtime = self._stat_mtime()
        if not force and mtime == self._cfg_mtime:
            return self.cfg
        old = self.cfg
        self.cfg = load_config(self.config_path)
        self._cfg_mtime = mtime
        self._last_reload_check = time.monotonic()

        # 嵌入配置变了 -> 嵌入器置脏，下次使用重建
        if self.embedder.configure(self.cfg["embedding"]):
            logger.info("嵌入配置变更，嵌入器将重建")

        logger.info(
            "配置热重载完成：top_k=%s, 容量=%s, τ=%sd, 检索权重=%s",
            self.cfg["retrieval"]["top_k"], self.cfg["memory"]["capacity_threshold"],
            self.cfg["retrieval"]["tau_days"], self.cfg["retrieval"]["weights"],
        )
        _ = old
        return self.cfg

    def _maybe_reload_config(self) -> None:
        """节流式自动检测：配置 mtime 变化即重载，无需重启。"""
        interval = float(self.cfg.get("reload", {}).get("check_interval_seconds", 2.0))
        now = time.monotonic()
        if now - self._last_reload_check < interval:
            return
        self._last_reload_check = now
        if self._stat_mtime() != self._cfg_mtime:
            self.reload_config()

    # ================= 并发嵌入（锁外执行 + 合批） =================
    async def _embed_async(self, texts: List[str]) -> np.ndarray:
        """嵌入入口：窗口内合批；window<=0 时直接同步编码。"""
        window_ms = int(self.cfg.get("parallelism", {}).get("embed_batch_window_ms", 0))
        if window_ms <= 0:
            return self.embedder.embed(texts)
        return await self._batcher.embed_async(texts, window_ms)

    # ================= 核心插入 =================
    async def insert(self, content: str, importance: float = 0.0) -> Optional[MemoryItem]:
        """插入一条记忆。与已有记忆（热/温区）相似度过高则视为重复：合并访问统计并返回 None。

        并发优化：嵌入在锁外完成（可合批、可重叠），锁内只做查重/落库/淘汰。
        """
        self._maybe_reload_config()
        # 向量生成放锁外：慢环节（真实模型）可与其它并发调用重叠，并进入合批窗口
        vec = (await self._embed_async([content]))[0]

        async with self._get_lock():
            now = time.time()
            item_id = self._gen_id(content)

            # 热/温区查重（旧版只查热区，这里扩展到温区，命中即合并）
            for pool in (self.hot_memory, self.warm_memory):
                for x in pool:
                    if self._similarity_dummy(content, x.content) >= self.cfg["memory"]["sim_threshold_hot"]:
                        x.last_access = now
                        x.access_count += 1
                        logger.info("重复记忆 %s 已合并（访问统计 +1）", x.item_id)
                        return None

            new_item = MemoryItem(
                item_id=item_id,
                content=content,
                timestamp=now,
                last_access=now,
                token_count=self._count_token(content),
                access_count=1,
                importance=float(importance),
            )

            self.vector_store.upsert(item_id, vec)
            self.hot_memory.append(new_item)
            await self._evict_check()
            return new_item

    # ================= 核心淘汰（修复爆炸 + 蒸馏 + 热度） =================
    async def _evict_check(self):
        self._maybe_reload_config()
        mem_cfg = self.cfg["memory"]
        t_now = time.time()
        ttl_sec = mem_cfg["hot_ttl_hours"] * 3600

        # 1. 热区超时 -> 温区
        expired = []
        stay_hot = []
        for m in self.hot_memory:
            if t_now - m.timestamp > ttl_sec:
                expired.append(m)
            else:
                stay_hot.append(m)
        self.hot_memory = stay_hot
        self.warm_memory.extend(expired)

        # 2. 强制限制热区条目（LRU -> 冷）
        while len(self.hot_memory) > mem_cfg["max_hot_entries"]:
            self.hot_memory.sort(key=lambda x: x.last_access)
            evicted = self.hot_memory.pop(0)
            await self._dump_cold(evicted)

        # 3. 强制限制温区条目
        while len(self.warm_memory) > mem_cfg["max_warm_entries"]:
            self.warm_memory.sort(key=lambda x: x.last_access)
            evicted = self.warm_memory.pop(0)
            await self._dump_cold(evicted)

        # 4. 限制冷存储大小
        await self._trim_cold_storage(mem_cfg["max_cold_entries"])

        # 5. 容量治理：总数超阈值 -> 先蒸馏，再淘汰最低热度
        await self._capacity_governance()

        # 5.5 节流落盘冷库热度增量（默认 30s 一次；避免每次检索都重写冷库）
        fold_interval = float(self.cfg.get("heat", {}).get("cold_fold_interval_seconds", 30))
        if self._cold_stat_bumps and time.time() - self._last_cold_fold > fold_interval:
            await self._fold_cold_stats()

        # 6. 异步落盘温存 + 向量（向量节流落盘，防 O(n²) 写盘）
        await self._save_warm()
        self._maybe_save_vectors()

    async def _capacity_governance(self) -> None:
        """治理：记忆总数超过 容量+余量 或磁盘超配额时执行。

        1) 计数治理：先蒸馏（占位）相似记忆，再淘汰热度最低，回落至容量；
        2) 磁盘配额：超警告线 -> 节流告警；超硬线 -> 按热度淘汰冷库回落。
        """
        mem_cfg = self.cfg["memory"]
        capacity = int(mem_cfg["capacity_threshold"])
        headroom = self._governance_headroom(capacity)
        total = self._count_total()

        if total > capacity + headroom:
            logger.info("记忆总数 %d 超过容量阈值 %d（含治理余量 %d），触发治理", total, capacity, headroom)
            if mem_cfg.get("distill_first", True):
                distilled = await self._try_distill()
                if distilled:
                    total = self._count_total()
            if total > capacity + headroom:
                removed = await self._evict_lowest_heat(total - capacity)
                logger.info("淘汰最低热度记忆 %d 条（现共 %d 条）", removed, self._count_total())

        # 磁盘配额（max_bytes=0 时立即返回，零开销）
        await self._quota_check()

    def _governance_headroom(self, capacity: int) -> int:
        """治理余量：让超容后的淘汰批量发生，而不是每次 insert 都全量治理。"""
        explicit = int(self.cfg.get("memory", {}).get("governance_headroom", 0))
        if explicit > 0:
            return explicit
        return max(10, min(capacity // 10, 200))

    def disk_usage(self) -> Dict[str, Any]:
        """磁盘占用明细（warm/cold/向量/索引）+ 配额比例，供告警与体检。

        注意：向量字节用"投影值"（内存中条数 × dim×4 字节），
        因为向量是节流落盘的，读磁盘文件会严重低估真实占用，
        配额保护的应是最终要写盘的量。
        """
        def size(p: str) -> int:
            try:
                return os.path.getsize(p) if os.path.exists(p) else 0
            except OSError:
                return 0

        vector_bytes = len(self.vector_store) * int(self.cfg["embedding"]["dim"]) * 4 + 128
        total = (
            size(self.path_warm)
            + size(self.path_cold)
            + vector_bytes
            + size(self.path_vector_index)
        )
        max_bytes = int(self.cfg.get("storage", {}).get("max_bytes", 0))
        return {
            "warm_bytes": size(self.path_warm),
            "cold_bytes": size(self.path_cold),
            "vector_bytes": vector_bytes,      # 投影值（未落盘也算）
            "index_bytes": size(self.path_vector_index),
            "total": total,
            "max_bytes": max_bytes,
            "ratio": (total / max_bytes) if max_bytes > 0 else 0.0,
        }

    async def _quota_check(self) -> None:
        """磁盘配额：超警告线 -> 告警一次；超硬线 -> 按热度淘汰冷库回落到 hard 线。"""
        storage_cfg = self.cfg.get("storage", {})
        max_bytes = int(storage_cfg.get("max_bytes", 0))
        if max_bytes <= 0:
            return
        usage = self.disk_usage()
        used = usage["total"]
        warn_at = max_bytes * float(storage_cfg.get("warn_ratio", 0.8))

        if used > warn_at:
            if not self._quota_warned:
                logger.warning(
                    "存储占用 %.1fMB 超过警告线 %.1fMB（硬线 %.1fMB，%.0f%%），"
                    "建议扩容或将 memory.capacity_threshold 调低",
                    used / 1e6, warn_at / 1e6, max_bytes / 1e6, used / max_bytes * 100,
                )
                self._quota_warned = True
        elif self._quota_warned:
            self._quota_warned = False

        if used > max_bytes:
            hard = max_bytes * float(storage_cfg.get("hard_ratio", 0.85))
            cold = await self._read_all_cold()
            if cold:
                cold.sort(key=lambda m: self._heat_score(m))
                per_item_vec = float(int(self.cfg["embedding"]["dim"]) * 4)  # float32
                need = 0
                freed = 0.0
                for m in cold:
                    if used - freed <= hard:
                        break
                    freed += len(m.content.encode("utf-8")) + 2 + per_item_vec
                    need += 1
                if need:
                    removed = await self._evict_lowest_heat(need)
                    logger.warning("磁盘配额硬线触发：淘汰 %d 条最低热度记忆释放磁盘", removed)

    def _maybe_save_vectors(self, force: bool = False) -> None:
        """向量落盘：节流（默认 5s）或强制（flush）；防 2 万量级 O(n²) 写盘。"""
        if not self.vector_store.dirty:
            return
        if force:
            self.vector_store.save()
            self._last_vec_save = time.time()
            return
        interval = float(self.cfg.get("storage", {}).get("vector_save_interval_seconds", 5.0))
        if time.time() - self._last_vec_save >= interval:
            self.vector_store.save()
            self._last_vec_save = time.time()

    # ---------- 蒸馏（占位接口，供后续接入 LLM） ----------
    async def _distill(self, cluster: List[MemoryItem]) -> Optional[MemoryItem]:
        """蒸馏占位：把一批相似记忆压缩为一条摘要。

        后续接入 LLM 时在这里实现，例如::

            texts = [m.content for m in cluster]
            summary_text = await self._llm_compress(texts)   # 你的 LLM 客户端
            return MemoryItem(item_id=self._gen_id(summary_text),
                              content=summary_text, timestamp=time.time(),
                              last_access=time.time(),
                              token_count=self._count_token(summary_text),
                              access_count=sum(m.access_count for m in cluster),
                              importance=max(m.importance for m in cluster))

        返回 None 表示本次不蒸馏（占位默认行为），直接进入淘汰阶段。
        """
        # TODO: 接入 LLM 压缩。当前为占位实现：不做任何事。
        return None

    async def _try_distill(self) -> int:
        """在低热度记忆里找相似簇（Jaccard 并查集），逐簇尝试蒸馏。

        返回净减少的条数（簇大小 - 摘要替换占位）。

        优化：占位版 _distill 恒返回 None（未接入 LLM），聚类纯属浪费，
        此时直接跳过聚类，等用户覆写 _distill 后自动启用。
        """
        if type(self)._distill is ThreeDogCoral._distill:
            return 0
        mem_cfg = self.cfg["memory"]
        sim_th = float(mem_cfg.get("distill_sim_threshold", 0.6))
        min_cluster = int(mem_cfg.get("distill_min_cluster", 3))

        # 候选池：全部冷记忆 + 温区（低热度优先，限制规模避免卡死）
        cold = await self._read_all_cold()
        for m in cold:
            if m.item_id in self._cold_stat_bumps:
                m.access_count += self._cold_stat_bumps[m.item_id]
                m.last_access = max(m.last_access, self._cold_last_access[m.item_id])
        candidates: List[MemoryItem] = cold + self.warm_memory
        pool_max = max((m.access_count for m in candidates), default=0)
        candidates.sort(key=lambda m: self._heat_score(m, pool_max))
        candidates = candidates[: 200]

        # 并查集按 Jaccard 聚类
        n = len(candidates)
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i in range(n):
            for j in range(i + 1, n):
                if self._similarity_dummy(candidates[i].content, candidates[j].content) >= sim_th:
                    union(i, j)

        clusters: Dict[int, List[MemoryItem]] = {}
        for idx, m in enumerate(candidates):
            clusters.setdefault(find(idx), []).append(m)

        removed_total = 0
        for members in clusters.values():
            if len(members) < min_cluster:
                continue
            summary = await self._distill(members)
            if summary is None:
                continue
            member_ids = {m.item_id for m in members}
            # 从各层移除成员
            self.hot_memory = [m for m in self.hot_memory if m.item_id not in member_ids]
            self.warm_memory = [m for m in self.warm_memory if m.item_id not in member_ids]
            await self._rewrite_cold(lambda rec: rec["item_id"] not in member_ids)
            self.vector_store.drop_many(list(member_ids))
            # 摘要以热记忆身份回归
            self.hot_memory.append(summary)
            vec = self.embedder.embed_one(summary.content)
            self.vector_store.upsert(summary.item_id, vec)
            removed_total += len(members) - 1
        return removed_total

    # ---------- 热度分 ----------
    def _heat_score(self, item: MemoryItem, pool_max_access: Optional[int] = None) -> float:
        """H = 访问频率(0.4) + 最近访问时间(0.3) + 用户显式重要性(0.3)。

        频率用对数刻度 log2(1+c)/log2(1+scale)，避免线性饱和；
        传入 pool_max_access 时按池内最大访问数归一化（跨池/跨项目可比）。
        """
        heat_cfg = self.cfg["heat"]
        w = heat_cfg["weights"]
        now = time.time()
        tau_sec = float(heat_cfg["tau_days"]) * 86400.0
        scale = max(float(heat_cfg.get("freq_scale", 10.0)), 1.0)
        if pool_max_access:
            scale = max(scale, float(pool_max_access))
        count = max(int(item.access_count), 0)
        freq = float(np.log2(1 + count) / np.log2(1 + scale))
        recency = float(np.exp(-(now - item.last_access) / tau_sec))
        importance = float(np.clip(item.importance, 0.0, 1.0))
        return float(w["frequency"] * freq + w["recency"] * recency + w["importance"] * importance)

    # ---------- 冷库热度增量合并 ----------
    def _merge_cold_bump(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        """把待合并的冷库热度增量并入一条冷记录（就地修改并返回）。"""
        iid = rec.get("item_id")
        if iid in self._cold_stat_bumps:
            rec["access_count"] = rec.get("access_count", 0) + self._cold_stat_bumps[iid]
            rec["last_access"] = max(
                float(rec.get("last_access", 0.0)),
                float(self._cold_last_access.get(iid, 0.0)),
            )
        return rec

    def _clear_cold_bumps(self) -> None:
        self._cold_stat_bumps.clear()
        self._cold_last_access.clear()

    async def _fold_cold_stats(self) -> int:
        """把待合并的冷库热度增量写回冷库 JSONL（全量重写，节流调用）。

        修复"冷库热度不落盘"：检索命中冷库记忆时只记增量，
        由这里（或各重写路径）合并回文件，重启后热度不丢。
        """
        if not self._cold_stat_bumps:
            return 0
        loop = asyncio.get_running_loop()
        bumps = dict(self._cold_stat_bumps)
        last_acc = dict(self._cold_last_access)

        def fold() -> int:
            if not os.path.exists(self.path_cold):
                return 0
            with open(self.path_cold, "r", encoding="utf-8") as f:
                lines = f.readlines()
            out = []
            folded = 0
            for ln in lines:
                line = ln.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    iid = rec.get("item_id")
                    if iid in bumps:
                        rec["access_count"] = rec.get("access_count", 0) + bumps[iid]
                        rec["last_access"] = max(
                            float(rec.get("last_access", 0.0)), float(last_acc.get(iid, 0.0))
                        )
                        folded += 1
                    out.append(json.dumps(rec, ensure_ascii=False) + "\n")
                except Exception:  # noqa: BLE001
                    out.append(ln)
            with open(self.path_cold, "w", encoding="utf-8") as f:
                f.writelines(out)
            return folded

        folded = await loop.run_in_executor(None, fold)
        self._clear_cold_bumps()
        self._last_cold_fold = time.time()
        if folded:
            logger.info("冷库热度增量已落盘：%d 条", folded)
        return folded

    # ---------- 淘汰最低热度 ----------
    async def _evict_lowest_heat(self, over: int) -> int:
        """从冷库（主要）、温区、热区依次淘汰热度最低的记忆，直到回落容量以内。

        淘汰是物理删除（不是热->冷的搬运），否则总数不会下降。
        冷库热度先合并未落盘增量，再按新鲜统计排序。
        """
        removed = 0
        # 1) 冷库
        cold = await self._read_all_cold()
        pool_max = max(
            (m.access_count for m in cold + self.warm_memory + self.hot_memory), default=0
        )
        if cold:
            for m in cold:
                if m.item_id in self._cold_stat_bumps:
                    m.access_count += self._cold_stat_bumps[m.item_id]
                    m.last_access = max(m.last_access, self._cold_last_access[m.item_id])
            cold.sort(key=lambda m: self._heat_score(m, pool_max))
            doomed = cold[:over]
            doomed_ids = {m.item_id for m in doomed}
            await self._rewrite_cold(lambda rec: rec["item_id"] not in doomed_ids)
            removed += len(doomed)
            # 内容哈希 id 可能被多行冷记录/热温区共享：只有完全消失才删向量
            remaining_ids = {m.item_id for m in cold if m.item_id not in doomed_ids}
            alive_ids = {m.item_id for m in self.hot_memory + self.warm_memory}
            drop_ids = [
                iid for iid in doomed_ids
                if iid not in remaining_ids and iid not in alive_ids
            ]
            if drop_ids:
                self.vector_store.drop_many(drop_ids)

        # 2) 温区
        while removed < over and self.warm_memory:
            self.warm_memory.sort(key=lambda m: self._heat_score(m, pool_max))
            m = self.warm_memory.pop(0)
            self.vector_store.drop_many([m.item_id])
            removed += 1

        # 3) 热区
        while removed < over and self.hot_memory:
            self.hot_memory.sort(key=lambda m: self._heat_score(m, pool_max))
            m = self.hot_memory.pop(0)
            self.vector_store.drop_many([m.item_id])
            removed += 1
        return removed

    def _count_total(self) -> int:
        return len(self.hot_memory) + len(self.warm_memory) + self._cold_count

    # ================= 冷存储 JSONL =================
    async def _dump_cold(self, item: MemoryItem):
        """逐行追加 JSON（沿用旧版命名），向量保留在 .npy 存储中并行存在。"""
        loop = asyncio.get_running_loop()
        line = json.dumps(asdict(item), ensure_ascii=False) + "\n"
        await loop.run_in_executor(
            None,
            lambda: open(self.path_cold, "a", encoding="utf-8").write(line),
        )
        self._cold_count += 1

    def _count_cold(self) -> int:
        if not os.path.exists(self.path_cold):
            return 0
        count = 0
        with open(self.path_cold, "r", encoding="utf-8") as f:
            for _ in f:
                count += 1
        return count

    async def _read_all_cold(self) -> List[MemoryItem]:
        if not os.path.exists(self.path_cold):
            return []
        loop = asyncio.get_running_loop()

        def read() -> List[MemoryItem]:
            items = []
            with open(self.path_cold, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        items.append(MemoryItem(**d))
                    except Exception:  # noqa: BLE001
                        continue
            return items

        return await loop.run_in_executor(None, read)

    async def _rewrite_cold(self, keep: Callable[[Dict[str, Any]], bool]) -> int:
        """按谓词过滤冷库（淘汰/蒸馏时使用）；顺带合并未落盘的冷库热度增量。

        返回过滤后剩余的条数。
        """
        loop = asyncio.get_running_loop()

        def rewrite() -> int:
            if not os.path.exists(self.path_cold):
                return 0
            with open(self.path_cold, "r", encoding="utf-8") as f:
                lines = f.readlines()
            kept = []
            for ln in lines:
                line = ln.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if keep(rec):
                        kept.append(json.dumps(self._merge_cold_bump(rec), ensure_ascii=False) + "\n")
                except Exception:  # noqa: BLE001
                    continue
            with open(self.path_cold, "w", encoding="utf-8") as f:
                f.writelines(kept)
            return len(kept)

        count = await loop.run_in_executor(None, rewrite)
        self._cold_count = count
        self._clear_cold_bumps()
        return count

    async def _trim_cold_storage(self, max_entries: int):
        """冷存储超过上限，只保留最新的 N 条；同步裁剪对应向量。"""
        if self._cold_count <= max_entries:
            return
        if not os.path.exists(self.path_cold):
            return
        loop = asyncio.get_running_loop()

        def trim() -> tuple:
            with open(self.path_cold, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) <= max_entries:
                return ([], [])
            kept_lines = lines[-max_entries:]
            removed_ids: List[str] = []
            for ln in lines[: len(lines) - max_entries]:
                try:
                    removed_ids.append(json.loads(ln.strip())["item_id"])
                except Exception:  # noqa: BLE001
                    continue
            kept_ids: List[str] = []
            kept: List[str] = []
            for ln in kept_lines:
                line = ln.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    kept_ids.append(rec.get("item_id", ""))
                    kept.append(json.dumps(self._merge_cold_bump(rec), ensure_ascii=False) + "\n")
                except Exception:  # noqa: BLE001
                    kept.append(ln)
            with open(self.path_cold, "w", encoding="utf-8") as f:
                f.writelines(kept)
            return (removed_ids, kept_ids)

        removed_ids, kept_ids = await loop.run_in_executor(None, trim)
        self._cold_count -= len(removed_ids)
        self._clear_cold_bumps()  # 已并入 kept（removed 的增量随条目消失）
        if removed_ids:
            # 只删"冷库已无同名行、热/温区也没有"的向量
            kept_set = set(kept_ids)
            alive_ids = {m.item_id for m in self.hot_memory + self.warm_memory}
            drop_ids = [iid for iid in removed_ids if iid not in kept_set and iid not in alive_ids]
            if drop_ids:
                self.vector_store.drop_many(drop_ids)

    # ================= 温存持久化 =================
    def _load_warm_sync(self):
        if os.path.exists(self.path_warm):
            try:
                with open(self.path_warm, "r", encoding="utf-8") as f:
                    raw = json.load(f) if os.path.getsize(self.path_warm) > 0 else []
                self.warm_memory = [MemoryItem(**d) for d in raw]
            except Exception as exc:  # noqa: BLE001
                logger.warning("温存加载失败（%s），从空温区开始", exc)

    async def _save_warm(self):
        data = [asdict(x) for x in self.warm_memory]
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: json.dump(
                data, open(self.path_warm, "w", encoding="utf-8"), ensure_ascii=False, indent=2
            ),
        )

    async def load_warm(self):
        """启动时调用（沿用旧版命名）。"""
        self._load_warm_sync()

    async def flush(self) -> None:
        """显式落盘：冷库热度增量 + 温存 + 向量（强制）。"""
        async with self._get_lock():
            await self._fold_cold_stats()
            await self._save_warm()
            self._maybe_save_vectors(force=True)

    # ================= 多路融合检索 =================
    async def search(self, query: str, top_k: Optional[int] = None) -> List[SearchHit]:
        """多路融合检索：向量余弦(0.6) + 关键词 Jaccard(0.2) + 时间衰减(0.2)。

        返回 Top-K 的 SearchHit，每条带综合得分与分项得分。
        命中条目会累加访问统计（提升其热度分）。

        并发/性能优化：
        - 查询嵌入在锁外完成（可合批/重叠，见 parallelism.embed_batch_window_ms）；
        - 向量打分整池一次 np.stack + 矩阵乘（BLAS），Jaccard 一次位图向量化；
        - 条目位图跨检索缓存（缓存命中时零分词）。
        """
        self._maybe_reload_config()
        query_vec = (await self._embed_async([query]))[0]

        async with self._get_lock():
            ret_cfg = self.cfg["retrieval"]
            weights = ret_cfg["weights"]
            tau_sec = float(ret_cfg["tau_days"]) * 86400.0
            k = int(top_k or ret_cfg["top_k"])
            now = time.time()
            query_norm = float(np.linalg.norm(query_vec)) or 1e-12

            use_bm = bool(ret_cfg.get("vectorized_jaccard", True)) and _HAS_BITCOUNT
            query_tokens = set(_hash_tokens(query))
            q_bits = _token_bitmap(_hash_tokens(query)) if use_bm else None

            def batch_jacs(items: List[MemoryItem]) -> Dict[str, float]:
                """一批条目的 Jaccard：位图路径一次向量化；否则逐条（查询只分词一次）。"""
                if not items:
                    return {}
                if use_bm:
                    for m in items:
                        if m.item_id not in self._bitmap_cache:
                            self._bitmap_cache[m.item_id] = _token_bitmap(_hash_tokens(m.content))
                    mat = np.stack([self._bitmap_cache[m.item_id] for m in items])
                    vals = _jaccard_many(q_bits, mat)
                    return {m.item_id: float(v) for m, v in zip(items, vals)}
                return {
                    m.item_id: _jaccard_similarity(query_tokens, set(_hash_tokens(m.content)))
                    for m in items
                }

            def batch_vecs(items: List[MemoryItem]) -> Dict[str, float]:
                """一批条目的向量余弦：一次矩阵乘算完（整池 BLAS）。"""
                ids, vecs = [], []
                for m in items:
                    v = self.vector_store.get(m.item_id)
                    if v is not None:
                        ids.append(m.item_id)
                        vecs.append(v)
                if not vecs:
                    return {}
                mat = np.stack(vecs)                                   # (N, dim)
                norms = np.linalg.norm(mat, axis=1)
                norms[norms == 0] = 1.0
                sims = (mat @ query_vec) / (norms * query_norm)        # (N,)
                return dict(zip(ids, map(float, sims)))

            # 快犬（热）+ 慢犬（温）：整池一次批量打分
            hot_warm = self.hot_memory + self.warm_memory
            jac_map = batch_jacs(hot_warm)
            vec_map = batch_vecs(hot_warm)

            def score(item: MemoryItem) -> SearchHit:
                vec_sim = vec_map.get(item.item_id, 0.0)
                jac = jac_map.get(item.item_id, 0.0)
                decay = float(np.exp(-(now - item.timestamp) / tau_sec))
                composite = float(weights["vector"] * vec_sim + weights["jaccard"] * jac + weights["time"] * decay)
                return SearchHit(
                    item=item,
                    score=composite,
                    scores={"vector": vec_sim, "jaccard": jac, "time": decay},
                )

            scored: List[SearchHit] = [score(m) for m in hot_warm]

            # 寻尸犬（冷）：总是扫描或仅在热温不足时扫描（由配置决定）
            include_cold = bool(ret_cfg.get("include_cold", True))
            if include_cold or len(scored) < k:
                cold_items = await self._search_cold(query, query_vec)
                if cold_items:
                    jac_map.update(batch_jacs(cold_items))
                    vec_map.update(batch_vecs(cold_items))
                    scored.extend(score(m) for m in cold_items)

            # 去重 + 过滤 + Top-K
            seen: Dict[str, SearchHit] = {}
            for hit in scored:
                if hit.item.item_id in seen:
                    if hit.score > seen[hit.item.item_id].score:
                        seen[hit.item.item_id] = hit
                else:
                    seen[hit.item.item_id] = hit
            ranked = sorted(seen.values(), key=lambda h: h.score, reverse=True)

            min_score = float(ret_cfg.get("min_score", 0.0))
            if min_score > 0:
                ranked = [h for h in ranked if h.score >= min_score]
            ranked = ranked[:k]

            # 命中统计：提升热度（热/温直接改内存并随 _save_warm 落盘；
            # 冷库条目记入待合并增量，由 _fold_cold_stats / 各重写路径写回）
            mem_ids = {m.item_id for m in self.hot_memory + self.warm_memory}
            for hit in ranked:
                hit.item.last_access = now
                hit.item.access_count += 1
                if hit.item.item_id not in mem_ids:
                    self._cold_stat_bumps[hit.item.item_id] = self._cold_stat_bumps.get(hit.item.item_id, 0) + 1
                    self._cold_last_access[hit.item.item_id] = now

            # 位图缓存兜底清理（防止淘汰后缓存无限膨胀）
            pool_size = len(self.hot_memory) + len(self.warm_memory) + self._cold_count
            if len(self._bitmap_cache) > 4 * pool_size + 1000:
                alive = {m.item_id for m in self.hot_memory + self.warm_memory}
                self._bitmap_cache = {iid: bm for iid, bm in self._bitmap_cache.items() if iid in alive}

            if self.vector_store.dirty:
                self._maybe_save_vectors()  # 检索补算的冷库向量：节流落盘
            return ranked

    async def _read_cold_tail(self, max_lines: int) -> List[MemoryItem]:
        """只读冷库尾部最新 max_lines 行（向后分块读取，不读全文件）。

        修复 2 万量级检索慢：原 readlines() 每次全读冷库再切片（30ms+），
        这里只读最后几个 64KB 块（~1-2ms）。
        """
        if not os.path.exists(self.path_cold):
            return []
        loop = asyncio.get_running_loop()

        def read_tail() -> List[MemoryItem]:
            size = os.path.getsize(self.path_cold)
            if size == 0:
                return []
            chunk = 65536
            pos = size
            raw = b""
            while True:
                pos = max(0, pos - chunk)
                with open(self.path_cold, "rb") as f:
                    f.seek(pos)
                    raw = f.read(chunk) + raw
                if pos == 0 or raw.count(b"\n") >= max_lines:
                    break
            text = raw.decode("utf-8", errors="replace")
            lines = text.splitlines()
            if pos > 0 and lines:
                lines = lines[1:]  # 从块中间切开 -> 首行不完整，丢弃
            items: List[MemoryItem] = []
            for ln in lines[-max_lines:]:
                try:
                    items.append(MemoryItem(**json.loads(ln)))
                except Exception:  # noqa: BLE001
                    continue
            return items

        return await loop.run_in_executor(None, read_tail)

    async def _search_cold(self, query: str, query_vec: np.ndarray) -> List[MemoryItem]:
        """冷库检索：只读尾部最新 N 行；向量缺失时现场生成并写回 .npy 缓存。

        只负责把冷库条目读出来并补齐向量，打分由调用方批量完成。
        """
        mem_cfg = self.cfg["memory"]
        scan_lines = int(mem_cfg.get("cold_scan_lines", 500))
        cold_items = await self._read_cold_tail(scan_lines)

        # 找出冷库里还没有向量的条目，批量补算（一次嵌入调用）
        missing = [m for m in cold_items if self.vector_store.get(m.item_id) is None]
        if missing:
            texts = [m.content for m in missing]
            vecs = self.embedder.embed(texts)
            for m, v in zip(missing, vecs):
                self.vector_store.upsert(m.item_id, v)
        return cold_items

    # ---------- 用户显式标记重要性 ----------
    async def mark_important(self, item_id: str, importance: float = 1.0) -> bool:
        """用户显式标记某条记忆的重要性（热度权重 0.3）。返回是否找到。"""
        async with self._get_lock():
            self._maybe_reload_config()
            importance = float(np.clip(importance, 0.0, 1.0))
            for pool in (self.hot_memory, self.warm_memory):
                for m in pool:
                    if m.item_id == item_id:
                        m.importance = importance
                        return True
            # 冷库
            found = await self._rewrite_cold_and_return(
                lambda rec: self._set_importance(rec, item_id, importance)
            )
            return found

    @staticmethod
    def _set_importance(rec: Dict[str, Any], item_id: str, importance: float) -> bool:
        if rec.get("item_id") == item_id:
            rec["importance"] = importance
            return True
        return False

    async def _rewrite_cold_and_return(self, mutate: Callable[[Dict[str, Any]], bool]) -> bool:
        """重写冷库并在 mutate 命中时改写该行；返回是否命中。"""
        loop = asyncio.get_running_loop()

        def rewrite() -> bool:
            if not os.path.exists(self.path_cold):
                return False
            hit = False
            with open(self.path_cold, "r", encoding="utf-8") as f:
                lines = f.readlines()
            out = []
            for ln in lines:
                line = ln.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if mutate(rec):
                        hit = True
                    out.append(json.dumps(self._merge_cold_bump(rec), ensure_ascii=False) + "\n")
                except Exception:  # noqa: BLE001
                    out.append(ln)
            with open(self.path_cold, "w", encoding="utf-8") as f:
                f.writelines(out)
            return hit

        result = await loop.run_in_executor(None, rewrite)
        self._clear_cold_bumps()
        return result

    # ---------- 熔断（沿用旧版） ----------
    def fuse_check(self, collected_items: List[MemoryItem]) -> bool:
        total = sum(i.token_count for i in collected_items)
        return total >= self.cfg["memory"]["token_fuse_threshold"]

    # ---------- 状态 ----------
    def stats(self) -> Dict[str, Any]:
        return {
            "hot": len(self.hot_memory),
            "warm": len(self.warm_memory),
            "cold": self._cold_count,   # 缓存计数（原 _count_cold 每次全量读文件，2 万量级 21ms/次）
            "total": self._count_total(),
            "vectors": len(self.vector_store),
            "capacity_threshold": self.cfg["memory"]["capacity_threshold"],
            "embedder": self.embedder._mode if self.embedder._resolved else self.cfg["embedding"]["embedder"],
        }


# ---------------------------------------------------------------------------
# 全局单例 + 两个 Agent 工具
# ---------------------------------------------------------------------------
_coral_instance: Optional[ThreeDogCoral] = None
_coral_instance_lock = threading.Lock()


def get_coral(config_path: Optional[str] = None) -> ThreeDogCoral:
    """全局单例（线程安全），供 Agent 工具与 Sidecar 共用。"""
    global _coral_instance
    with _coral_instance_lock:
        if _coral_instance is None:
            _coral_instance = ThreeDogCoral(config_path)
        return _coral_instance


@register_tool(
    name="memory_search",
    description="检索脑珊瑚记忆缓存 / Search the Brain Coral memory cache："
                "多路融合（向量 0.6 + 关键词 0.2 + 时间衰减 0.2）multi-fusion retrieval "
                "(vector 0.6 + keyword 0.2 + recency 0.2)，返回 Top-K 相关记忆及得分 (returns Top-K relevant memories with scores).",
    parameters={
        "query": {"type": "string", "required": True, "description": "查询文本"},
        "top_k": {"type": "integer", "required": False, "description": "返回条数，默认取配置(5)"},
    },
)
async def memory_search(query: str, top_k: Optional[int] = None) -> Dict[str, Any]:
    coral = get_coral()
    hits = await coral.search(query, top_k=top_k)
    return {
        "query": query,
        "count": len(hits),
        "hits": [
            {
                "id": h.item_id,
                "content": h.content,
                "score": round(float(h.score), 4),
                "scores": {k: round(float(v), 4) for k, v in h.scores.items()},
            }
            for h in hits
        ],
    }


@register_tool(
    name="memory_insert",
    description="向脑珊瑚记忆缓存插入一条记忆 / Insert a memory into the Brain Coral cache："
                "自动生成语义向量（维度随嵌入模型配置）auto-embeds with the configured model; "
                "参与热度淘汰 heat-based eviction; 相似重复自动合并 similar repeats auto-merge. "
                "注意：新记忆先入内存热区（重启丢失），如需持久化请随后调用 memory_flush "
                "(Note: new memories live in the in-memory hot zone and are lost on restart; call memory_flush to persist).",
    parameters={
        "content": {"type": "string", "required": True, "description": "记忆内容"},
        "importance": {"type": "number", "required": False, "description": "用户显式重要性 0~1，默认 0"},
    },
)
async def memory_insert(content: str, importance: float = 0.0) -> Dict[str, Any]:
    coral = get_coral()
    item = await coral.insert(content, importance=importance)
    return {
        "inserted": item is not None,
        "item_id": item.item_id if item else None,
        "message": "ok" if item else "重复记忆：已合并访问统计",
        "persisted": False,
        "hint": "新记忆先入内存热区（重启丢失）；如需持久化请调用 memory_flush",
    }


@register_tool(
    name="memory_flush",
    description="强制把脑珊瑚记忆落盘 / Force-flush the Brain Coral memory to disk："
                "热区记忆默认只存内存（进程/重启后丢失），调用后持久化到 memory_data/（温存 + 向量）。"
                "写重要记忆（偏好/结论/档案）后请调用它 (Persist hot-zone memories to disk; call after inserting important memories).",
    parameters={},
)
async def memory_flush() -> Dict[str, Any]:
    """把热区条目迁入温区并强制落盘——用户显式 flush = 明确要持久化这些记忆。"""
    coral = get_coral()
    moved = len(coral.hot_memory)
    if moved:
        # 热区不落盘是三级存储的设计；显式 flush 时把热区条目并入温区一起持久化
        coral.warm_memory.extend(coral.hot_memory)
        coral.hot_memory = []
    await coral.flush()
    s = coral.stats()
    return {"flushed": True, "hot_persisted": moved, "stats": s}


# ---------------------------------------------------------------------------
# DSH 桥接：导出 defineTool 定义 / 生成 cordis 插件 JS / 本地 RPC Sidecar
# ---------------------------------------------------------------------------
def export_dsh_tool_definitions() -> List[Dict[str, Any]]:
    """导出为 DSH `harness.defineTool` 的 JSON 形态（不含 execute，见 build_dsh_cordis_plugin_js）。"""
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
            "output": {"schema": {"type": "object"}},
        }
        for spec in TOOL_REGISTRY.values()
    ]


def build_dsh_cordis_plugin_js(sidecar_url: str = "http://127.0.0.1:8765/rpc") -> str:
    """生成 DSH cordis 插件源码：harness.registerTool(ctx, harness.defineTool({...}))。

    execute 通过 HTTP POST 把调用转发给 MemoryToolSidecar（Python 侧执行注册的工具）。
    把返回的 JS 字符串喂给 Agent 的 cordis_define 工具（或写进 cordis.yml 的插件）即可。
    """
    lines: List[str] = ["return (ctx) => {", "  // coral-memory (author: @Ne) —— 二次开发请保留此注释"]
    for spec in TOOL_REGISTRY.values():
        parameters_js = json.dumps(spec.parameters, ensure_ascii=False)
        lines.append(
            "  harness.registerTool(ctx, harness.defineTool({\n"
            f"    name: {spec.name!r},\n"
            f"    description: {spec.description!r},\n"
            f"    parameters: {parameters_js},\n"
            "    output: {\n"
            "      schema: { type: 'object' },\n"
            "      render(_args, value) {\n"
            "        return [{ type: 'text', text: JSON.stringify(value) }]\n"
            "      }\n"
            "    },\n"
            "    async execute(args) {\n"
            f"      const res = await fetch({sidecar_url!r}, {{\n"
            "        method: 'POST',\n"
            "        headers: { 'content-type': 'application/json' },\n"
            f"        body: JSON.stringify({{ tool: {spec.name!r}, args }})\n"
            "      })\n"
            "      return await res.json()\n"
            "    }\n"
            "  }))\n"
        )
    lines.append("}")
    return "\n".join(lines)


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if hasattr(value, "to_dict"):
        return _to_jsonable(value.to_dict())
    return str(value)


class MemoryToolSidecar:
    """极简 HTTP 桥：DSH 的 JS 工具 -> POST /rpc -> Python @register_tool 注册表。

    用法::

        sidecar = MemoryToolSidecar(port=8765)
        sidecar.start()
        plugin_js = build_dsh_cordis_plugin_js("http://127.0.0.1:8765/rpc")
        # 把 plugin_js 交给 DSH 的 cordis_define ...
        sidecar.stop()
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # ---------- 生命周期 ----------
    def start(self) -> None:
        if self._server is not None:
            return
        sidecar = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: Any) -> None:
                return

            def do_POST(self) -> None:  # noqa: N802 (http.server 命名)
                length = int(self.headers.get("content-length", "0"))
                body = self.rfile.read(length) if length else b"{}"
                try:
                    req = json.loads(body or b"{}")
                    tool = req.get("tool")
                    args = req.get("args") or {}
                    spec = TOOL_REGISTRY.get(tool)
                    if spec is None:
                        self._reply(404, {"ok": False, "error": f"unknown tool: {tool}"})
                        return
                    result = asyncio.run(spec.fn(**_to_jsonable(args)))
                    self._reply(200, {"ok": True, "result": _to_jsonable(result)})
                except Exception as exc:  # noqa: BLE001
                    self._reply(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

            def _reply(self, code: int, payload: Dict[str, Any]) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("content-type", "application/json; charset=utf-8")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info("MemoryToolSidecar 已启动：http://%s:%s/rpc", self.host, self.port)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


# ---------------------------------------------------------------------------
# 便捷入口
# ---------------------------------------------------------------------------
async def run_demo() -> None:
    """直接运行本文件即可体验脑珊瑚的完整流程。"""
    from example_usage import main as example_main

    await example_main()


def sidecar_main(
    config_path: Optional[str] = None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    """命令行入口：启动 MemoryToolSidecar，把 @register_tool 工具桥接给 DSH 插件。

    pip 安装后可用 `coral-sidecar` 直接启动；也可 `python -m three_dog_coral.sidecar`。
    用法: coral-sidecar [--config coral_config.json] [--host 127.0.0.1] [--port 8765]
    """
    import argparse

    parser = argparse.ArgumentParser(description="coral-memory sidecar: 桥接 @register_tool 工具给 DSH 插件")
    parser.add_argument("--config", default=None, help="coral_config.json 路径（默认当前目录，缺失自动生成）")
    parser.add_argument("--host", default=host)
    parser.add_argument("--port", type=int, default=port)
    args, _ = parser.parse_known_args()

    cfg_path = args.config or config_path or os.path.join(os.getcwd(), "coral_config.json")
    get_coral(cfg_path)  # 预热单例（缺配置自动生成默认）
    sidecar = MemoryToolSidecar(args.host, args.port)
    sidecar.start()
    print(f"coral-memory sidecar 已启动: http://{args.host}:{args.port}/rpc")
    print(f"可用工具: {', '.join(TOOL_REGISTRY)}")
    print("把 dist/coral_plugin.js（或 build_dsh_cordis_plugin_js() 的输出）")
    print("交给 Agent 的 cordis_define 即可注册为 DSH 工具。Ctrl+C 退出。")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        sidecar.stop()
        print("sidecar 已停止")


if __name__ == "__main__":
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 终端中文不乱码
    except Exception:  # noqa: BLE001
        pass
    for _noisy in ("httpx", "huggingface_hub", "sentence_transformers", "urllib3"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run_demo())
