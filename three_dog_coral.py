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
import atexit
import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.request
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
    "ThreadItem",
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

# 推理线索链路落盘节流（秒）：突发批量操作合并写盘，真实聊天操作（间隔 >> 此值）仍即时落盘
_THREADS_SAVE_DEBOUNCE = 0.25

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
    # 推理线索链路（Thread）：概括式、永不遗忘的跨聊天协作线索。
    # 与热/温/冷记忆池完全独立：不参与热度淘汰 / 容量治理 / 磁盘配额（"永不遗忘"），
    # 每次变更原子写盘，读取时按文件 mtime 检测外部变更（多进程/多聊天共享同一份链路）。
    "threads": {
        "path": "memory_data/coral_threads.json",
    },
    # 蒸馏 LLM 端点（OpenAI 兼容 /chat/completions；base_url+api_key 齐备时自动启用蒸馏）。
    # api_key 仅存本机，只用于把相似记忆压缩成摘要，绝不外发。
    "llm": {
        "base_url": "",          # 如 https://api.deepseek.com/v1
        "api_key": "",           # 如 sk-...
        "model": "deepseek-chat",
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
    """读取 coral_config.json，与默认值深合并；文件缺失/损坏时回退默认。

    注意：必须以 DEFAULT_CONFIG 的**深拷贝**为基底（_recursive_merge 只深合并
    override 分支，用户配置缺省的段会直接共享 DEFAULT_CONFIG 对象——运行时
    config_set 会污染模块级默认值，导致"恢复默认"失效）。
    """
    path = path or DEFAULT_CONFIG_PATH
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # 深拷贝：防止 cfg 与 DEFAULT_CONFIG 共享引用
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


@dataclass
class ThreadItem:
    """推理线索链路（Thread）：概括式、永不遗忘的跨聊天协作线索。

    与普通记忆（热/温/冷池，参与热度淘汰）有本质区别：
    - 存独立文件（coral_threads.json），每次变更原子落盘；
    - 不参与热度淘汰 / 容量治理 / 磁盘配额 —— "永不遗忘"；
    - 读取时按文件 mtime 检测外部变更（多个聊天进程共享同一份链路，
      聊天 A 建的链路，聊天 B~F 立刻可见、各自推进）。
    """

    thread_id: str
    title: str                                              # 链路名（短短语）
    summary: str = ""                                       # 宏观路径/当前状态（几句话）
    status: str = "active"                                  # active | interrupted | archived
    steps: List[Dict[str, Any]] = field(default_factory=list)  # 推进记录链 {seq,text,done,at,by}
    parent_thread_id: Optional[str] = None                  # 父链路 ID（线索链串联）
    created_at: float = 0.0
    updated_at: float = 0.0
    last_advance_by: str = ""                               # 最近推进者（如 聊天B）


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
        self.path_threads = self.cfg.get("threads", {}).get("path", "memory_data/coral_threads.json")

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

        # 推理线索链路（永不遗忘，独立于记忆池；启动即加载）
        self._threads: Dict[str, ThreadItem] = {}
        self._threads_fp: Optional[tuple] = None   # 文件指纹 (mtime, size)，检测外部变更
        self._threads_dirty = False                # 变更未落盘标记（节流）
        self._threads_last_save = 0.0
        self._load_threads_sync()
        atexit.register(self._flush_threads_atexit)

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

    # ---------- 蒸馏（LLM 压缩相似记忆；未配置端点时保持占位行为） ----------
    async def _distill(self, cluster: List[MemoryItem]) -> Optional[MemoryItem]:
        """把一批相似记忆压缩为一条摘要（LLM 压缩）。

        LLM 端点从配置 `llm` 段读取（OpenAI 兼容 /chat/completions，urllib 零依赖）：
            "llm": {"base_url": "https://api.deepseek.com/v1",
                    "api_key": "sk-...",
                    "model": "deepseek-chat"}
        未配置 base_url/api_key 时返回 None（保持占位行为，直接进入淘汰阶段）。

        摘要继承簇的热度/重要性；失败或超时自动降级为"不蒸馏"，绝不阻断治理。
        """
        llm_cfg = self.cfg.get("llm", {})
        base = str(llm_cfg.get("base_url") or "").strip()
        api_key = str(llm_cfg.get("api_key") or "").strip()
        if not base or not api_key or not cluster:
            return None
        model = str(llm_cfg.get("model") or "deepseek-chat").strip()
        texts = [f"- {m.content}" for m in cluster]
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你是记忆压缩助手：把相似记忆合并成一条不超过 80 字的摘要，保留关键事实与数字，不要遗漏重要结论。直接输出摘要本身，不要任何思考过程、解释或前缀。"},
                {"role": "user", "content": "压缩下面这些相似记忆:\n" + "\n".join(texts)},
            ],
            "temperature": 0.2,
            # 推理模型（deepseek-v4-*）的思考过程也占 max_tokens，
            # 太小会被思考占满导致 content 为空；1024 保证摘要必出。
            "max_tokens": 1024,
        }
        req = urllib.request.Request(
            base.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json", "authorization": f"Bearer {api_key}"},
        )
        try:
            loop = asyncio.get_running_loop()
            resp = await loop.run_in_executor(
                None, lambda: urllib.request.urlopen(req, timeout=30).read()
            )
            data = json.loads(resp.decode("utf-8"))
            summary = data["choices"][0]["message"]["content"].strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("蒸馏 LLM 调用失败（%s），跳过本次蒸馏", exc)
            return None
        if not summary:
            return None
        now = time.time()
        return MemoryItem(
            item_id=self._gen_id(summary),
            content=summary,
            timestamp=now,
            last_access=now,
            token_count=self._count_token(summary),
            access_count=sum(m.access_count for m in cluster),   # 热度继承
            importance=max(m.importance for m in cluster),       # 重要性继承
        )

    async def _try_distill(self) -> int:
        """在低热度记忆里找相似簇（Jaccard 并查集），逐簇尝试蒸馏。

        返回净减少的条数（簇大小 - 摘要替换占位）。

        优化：未配置 LLM 端点（base_url/api_key 为空）时蒸馏无意义，
        直接跳过聚类，等配置 `llm` 段后自动启用。
        """
        llm_cfg = self.cfg.get("llm", {})
        if not str(llm_cfg.get("base_url") or "").strip() or not str(llm_cfg.get("api_key") or "").strip():
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
        """冷存储超过上限，按热度裁剪（保留热度最高的 N 条），同步裁剪对应向量。

        旧版按写入顺序保留最新 N 条（纯 FIFO，不看价值）——旧的重要记忆会被无脑裁掉；
        新版合并未落盘热度增量后按热度分排序，保留热度最高的 N 条：
        高频/重要/最近访问的记忆留在冷库，真正冷门的先被淘汰。
        """
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
            # 解析为 MemoryItem（合并未落盘热度增量），按热度升序排列
            items: List[MemoryItem] = []
            bad_lines: List[str] = []
            for ln in lines:
                line = ln.strip()
                if not line:
                    continue
                try:
                    m = MemoryItem(**json.loads(line))
                    if m.item_id in self._cold_stat_bumps:
                        m.access_count += self._cold_stat_bumps[m.item_id]
                        m.last_access = max(m.last_access, self._cold_last_access[m.item_id])
                    items.append(m)
                except Exception:  # noqa: BLE001
                    bad_lines.append(ln)
            if not items:
                return ([], [])
            items.sort(key=lambda m: self._heat_score(m))
            # 淘汰热度最低的（len - max_entries）条，保留热度最高的 max_entries 条
            doomed = items[: len(items) - max_entries]
            kept = items[len(items) - max_entries:]
            removed_ids = [m.item_id for m in doomed]
            kept_ids = [m.item_id for m in kept]
            lines_out = [json.dumps(asdict(m), ensure_ascii=False) + "\n" for m in kept]
            lines_out.extend(bad_lines)  # 坏行原样保留，绝不丢数据
            with open(self.path_cold, "w", encoding="utf-8") as f:
                f.writelines(lines_out)
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
        """显式落盘：冷库热度增量 + 温存 + 向量（强制）+ 链路（强制，越过节流）。"""
        async with self._get_lock():
            await self._fold_cold_stats()
            await self._save_warm()
            self._maybe_save_vectors(force=True)
            self._write_threads_now()

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

    # ---------- 删除 ----------
    async def delete(self, item_id: str) -> bool:
        """按 item_id 删除一条记忆（热/温/冷 + 向量库）。返回是否找到并删除。"""
        async with self._get_lock():
            self._maybe_reload_config()
            found = False
            # 热区
            before = len(self.hot_memory)
            self.hot_memory = [m for m in self.hot_memory if m.item_id != item_id]
            found = found or len(self.hot_memory) != before
            # 温区
            before = len(self.warm_memory)
            self.warm_memory = [m for m in self.warm_memory if m.item_id != item_id]
            found = found or len(self.warm_memory) != before
            # 冷区（存在同名行才算找到）
            if os.path.exists(self.path_cold):
                def count_hits() -> int:
                    hits = 0
                    with open(self.path_cold, "r", encoding="utf-8") as f:
                        for ln in f:
                            line = ln.strip()
                            if not line:
                                continue
                            try:
                                if json.loads(line).get("item_id") == item_id:
                                    hits += 1
                            except Exception:  # noqa: BLE001
                                continue
                    return hits
                loop = asyncio.get_running_loop()
                hits = await loop.run_in_executor(None, count_hits)
                if hits:
                    found = True
                    await self._rewrite_cold(lambda rec: rec.get("item_id") != item_id)
            # 向量：内容哈希 id 可能被共享，只有热/温/冷均无引用才删
            if found and item_id not in {m.item_id for m in self.hot_memory + self.warm_memory}:
                self.vector_store.drop_many([item_id])
            if found:
                await self._save_warm()
                self._maybe_save_vectors(force=True)
            return found

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

    # ================= 推理线索链路（Thread，永不遗忘） =================
    def _threads_file_fp(self) -> Optional[tuple]:
        """链路文件指纹 (mtime, size)。size 参与比对：某些文件系统 mtime
        粒度粗（同一 tick 内两次写盘 mtime 相同），而任何变更都会改文件大小。"""
        try:
            st = os.stat(self.path_threads)
            return (st.st_mtime, st.st_size)
        except OSError:
            return None

    def _load_threads_sync(self) -> None:
        """启动/外部变更时加载链路文件。文件缺失视为空。"""
        self._threads = {}
        if not os.path.exists(self.path_threads):
            self._threads_fp = None
            return
        try:
            with open(self.path_threads, "r", encoding="utf-8") as f:
                raw = json.load(f) if os.path.getsize(self.path_threads) > 0 else []
            items = raw if isinstance(raw, list) else list(raw.values())
            for d in items:
                try:
                    t = ThreadItem(**d)
                    self._threads[t.thread_id] = t
                except Exception:  # noqa: BLE001
                    continue
            self._threads_fp = self._threads_file_fp()
        except Exception as exc:  # noqa: BLE001
            logger.warning("链路文件加载失败（%s），从空链路开始", exc)

    def _threads_changed_on_disk(self) -> bool:
        """其它聊天进程可能改写了链路文件：指纹变了就重载（共享协作的同步机制）。"""
        return self._threads_file_fp() != self._threads_fp

    def _sync_threads(self) -> None:
        """读/写路径先检测外部变更，保证多进程（多聊天）看到同一份链路。"""
        if self._threads_changed_on_disk():
            self._load_threads_sync()

    def _save_threads(self) -> None:
        """变更路径：标记脏 + 节流落盘（THREADS_SAVE_DEBOUNCE 秒内合并为一次写盘）。

        突发批量操作（压测/脚本循环）不再每次全量重写文件，避免 O(n²) 落盘；
        真实使用（聊天驱动，操作间隔 >> 250ms）下每次变更仍会立即落盘。
        落盘兜底：flush() 强制 + 进程正常退出 atexit 强制。
        """
        self._threads_dirty = True
        if time.monotonic() - self._threads_last_save >= _THREADS_SAVE_DEBOUNCE:
            self._write_threads_now()

    def _write_threads_now(self) -> None:
        """立即原子写链路文件：tmp + os.replace（写前做一次远端合并）。

        多进程（多聊天）并发写同一文件时，用锁文件（O_EXCL）串行化写者，
        锁内合并远端步骤再写盘 —— 杜绝 Windows 上 os.replace 撞文件（WinError 32）
        以及"后写者覆盖先写者步骤"的丢更新。
        """
        if not self._threads_dirty and self._threads_last_save > 0:
            return
        lock_path = self.path_threads + ".lock"
        acquired = False
        deadline = time.time() + 10.0
        while time.time() < deadline:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("utf-8"))
                os.close(fd)
                acquired = True
                break
            except FileExistsError:
                # stale 检测：锁文件超 10s 未更新 → 视为崩溃遗留，强抢
                try:
                    if time.time() - os.path.getmtime(lock_path) > 10:
                        os.remove(lock_path)
                        continue
                except OSError:
                    pass
                time.sleep(0.005)
        if not acquired:
            logger.warning("链路写锁获取超时，本次落盘跳过（数据仍在内存，flush 或下次变更会再写）")
            return
        try:
            # 写前"远端合并"：若另一个聊天进程在我们本次操作期间刚写入（指纹变了），
            # 以远端为基底把本地多出的步骤补进去（按 seq 排序），避免后写者覆盖先写者的推进步骤。
            # （_threads_fp 为 None 表示磁盘上还没有文件，无远端可合并）
            if self._threads_fp is not None and self._threads_changed_on_disk():
                try:
                    with open(self.path_threads, "r", encoding="utf-8") as f:
                        raw = json.load(f) if os.path.getsize(self.path_threads) > 0 else []
                    remote = {t.thread_id: t for t in (ThreadItem(**d) for d in raw)}
                    for tid, t in self._threads.items():
                        rt = remote.get(tid)
                        if rt is None or not t.steps:
                            continue
                        # 按 step_id 合并（旧数据无 step_id 时回退 seq|at），
                        # 不同写者并发产生的步骤全部保留，各自内部顺序不变
                        def _sid(s: Dict[str, Any]) -> str:
                            return str(s.get("step_id") or f"{s.get('seq')}|{s.get('at')}")

                        remote_ids = {_sid(s) for s in rt.steps}
                        extra = [s for s in t.steps if _sid(s) not in remote_ids]
                        if extra:
                            t.steps = sorted(
                                rt.steps + extra,
                                key=lambda s: (s.get("seq", 0), s.get("at", 0.0)),
                            )
                except Exception as exc:  # noqa: BLE001 远端合并失败不影响写盘（last-write-wins 兜底）
                    logger.warning("链路远端合并失败（%s），使用本地状态直接写盘", exc)
            data = [asdict(t) for t in self._threads.values()]
            tmp = self.path_threads + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            # 重试兜底：即使持锁，读者瞬时的读句柄也可能挡住 os.replace（WinError 32）
            for attempt in range(8):
                try:
                    os.replace(tmp, self.path_threads)
                    break
                except PermissionError:
                    if attempt == 7:
                        raise
                    time.sleep(0.01 * (attempt + 1))
            self._threads_fp = self._threads_file_fp()
            self._threads_last_save = time.monotonic()
            self._threads_dirty = False
        finally:
            try:
                os.remove(lock_path)
            except OSError:
                pass

    def _flush_threads_atexit(self) -> None:
        """进程正常退出时兜底落盘（仅当还有未落盘变更；崩溃/强杀不保证，日常退出可靠）。"""
        if not self._threads_dirty:
            return
        try:
            self._write_threads_now()
        except Exception:  # noqa: BLE001
            pass

    def _thread_view(self, t: ThreadItem) -> Dict[str, Any]:
        """链路的可序列化视图（给 Agent 渲染聊天内看板用）。"""
        return {
            "thread_id": t.thread_id,
            "title": t.title,
            "summary": t.summary,
            "status": t.status,
            "parent_thread_id": t.parent_thread_id,
            "last_advance_by": t.last_advance_by,
            "created_at": round(t.created_at, 3),
            "updated_at": round(t.updated_at, 3),
            "step_count": len(t.steps),
            "steps": [
                {
                    "step_id": s.get("step_id"),
                    "seq": s.get("seq"),
                    "text": s.get("text", ""),
                    "done": bool(s.get("done")),
                    "by": s.get("by", ""),
                    "at": round(float(s.get("at", 0.0)), 3),
                }
                for s in t.steps
            ],
        }

    async def thread_create(
        self,
        title: str,
        summary: str = "",
        parent_thread_id: Optional[str] = None,
        by: str = "",
    ) -> ThreadItem:
        """创建一条推理线索链路（永不遗忘，不参与热度淘汰）。返回新链路。"""
        async with self._get_lock():
            self._sync_threads()
            title = (title or "").strip()
            if not title:
                raise ValueError("title 不能为空")
            if parent_thread_id and parent_thread_id not in self._threads:
                raise KeyError(f"父链路不存在: {parent_thread_id}")
            now = time.time()
            thread = ThreadItem(
                thread_id=self._gen_id(f"{title}|{now}"),
                title=title[:80],
                summary=(summary or "").strip(),
                status="active",
                parent_thread_id=parent_thread_id,
                created_at=now,
                updated_at=now,
                last_advance_by=(by or "").strip(),
            )
            self._threads[thread.thread_id] = thread
            self._save_threads()
            return thread

    async def thread_status(
        self,
        thread_id: Optional[str] = None,
        include_archived: bool = False,
        query: Optional[str] = None,
    ) -> List[ThreadItem]:
        """查看链路状态。

        - thread_id 缺省：返回全部链路（默认不含已归档），按 active→interrupted→archived、
          updated_at 倒序 —— 跨聊天协作的"宏观视图"；
        - thread_id 指定：返回该链路（含已归档），找不到返回空列表；
        - query：按标题/摘要关键词过滤（大小写不敏感）。
        """
        async with self._get_lock():
            self._sync_threads()
            if thread_id:
                t = self._threads.get(thread_id)
                return [t] if t else []
            threads = list(self._threads.values())
            if not include_archived:
                threads = [t for t in threads if t.status != "archived"]
            if query:
                q = query.strip().lower()
                threads = [t for t in threads if q in t.title.lower() or q in t.summary.lower()]
            order = {"active": 0, "interrupted": 1, "archived": 2}
            threads.sort(key=lambda t: (order.get(t.status, 9), -t.updated_at))
            return threads

    def _thread_step(self, thread_id: str, text: str, done: bool, by: str, now: float, seq: int) -> Dict[str, Any]:
        """构造一个步骤节点。step_id 全局唯一（md5(thread|text|by|time)）——
        多进程并发推进时按 step_id 合并，避免不同写者的同号步骤互相覆盖。"""
        return {
            "step_id": hashlib.md5(f"{thread_id}|{text}|{by}|{now}".encode("utf-8")).hexdigest()[:12],
            "seq": seq,
            "text": text,
            "done": bool(done),
            "at": now,
            "by": (by or "").strip(),
        }

    async def thread_advance(self, thread_id: str, note: str, done: bool = False, by: str = "") -> ThreadItem:
        """推进一条链路：追加一个步骤节点（谁在何时推进了什么）。聊天 B~F 各自推进协作。"""
        async with self._get_lock():
            self._sync_threads()
            t = self._threads.get(thread_id)
            if t is None:
                raise KeyError(f"链路不存在: {thread_id}")
            note = (note or "").strip()
            if not note:
                raise ValueError("note 不能为空")
            now = time.time()
            t.steps.append(self._thread_step(
                thread_id, note[:500], done, by, now, len(t.steps) + 1
            ))
            t.updated_at = now
            if by:
                t.last_advance_by = by.strip()
            self._save_threads()
            return t

    async def thread_interrupt(self, thread_id: str, reason: str = "") -> ThreadItem:
        """中断一条链路：状态 → interrupted；内容全部保留（永不遗忘），可随时 resume。"""
        async with self._get_lock():
            self._sync_threads()
            t = self._threads.get(thread_id)
            if t is None:
                raise KeyError(f"链路不存在: {thread_id}")
            t.status = "interrupted"
            t.updated_at = time.time()
            reason = (reason or "").strip()
            if reason:
                t.steps.append(self._thread_step(
                    thread_id, f"[中断] {reason[:500]}", False, "", time.time(), len(t.steps) + 1
                ))
            self._save_threads()
            return t

    async def thread_archive(self, thread_id: str) -> ThreadItem:
        """归档一条链路：状态 → archived（不再出现在活跃总览，内容永不遗忘）。"""
        async with self._get_lock():
            self._sync_threads()
            t = self._threads.get(thread_id)
            if t is None:
                raise KeyError(f"链路不存在: {thread_id}")
            t.status = "archived"
            t.updated_at = time.time()
            self._save_threads()
            return t

    async def thread_resume(self, thread_id: str) -> ThreadItem:
        """恢复一条链路：状态 → active（中断/归档的链路重新开工）。"""
        async with self._get_lock():
            self._sync_threads()
            t = self._threads.get(thread_id)
            if t is None:
                raise KeyError(f"链路不存在: {thread_id}")
            t.status = "active"
            t.updated_at = time.time()
            self._save_threads()
            return t

    async def thread_link(self, child_id: str, parent_id: str) -> bool:
        """把两条链路串成父子关系（线索链）。返回是否成功。"""
        async with self._get_lock():
            self._sync_threads()
            if child_id not in self._threads or parent_id not in self._threads:
                raise KeyError("链路不存在")
            if child_id == parent_id:
                raise ValueError("不能把自己链接为自己")
            self._threads[child_id].parent_thread_id = parent_id
            self._threads[child_id].updated_at = time.time()
            self._save_threads()
            return True

    # ================= 配置管理（Agent 可调，管理上下文缓存） =================
    async def config_get(self, path: Optional[str] = None) -> Any:
        """查看配置。path 支持点分路径（如 retrieval.top_k / memory.capacity_threshold），
        缺省返回全量配置。"""
        async with self._get_lock():
            self._maybe_reload_config()
            if not path:
                return self.cfg
            node: Any = self.cfg
            for part in path.split("."):
                if not isinstance(node, dict) or part not in node:
                    raise KeyError(f"配置路径不存在: {path}")
                node = node[part]
            return node

    async def config_set(self, key_path: str, value: Any) -> Dict[str, Any]:
        """修改配置：即时生效（热加载语义）+ 原子写回 coral_config.json 持久化。

        管理上下文缓存的入口（容量/阈值/top_k/配额等）。受保护路径：
        - paths.* / threads.* ：运行时改路径会脱离现有数据目录，禁止；
        - embedding.* ：换模型/维度需重建向量，请用 migrate_bge.py，禁止；
        改 memory.capacity_threshold 会立即触发一次容量治理。
        """
        async with self._get_lock():
            self._maybe_reload_config()
            if key_path == "paths" or key_path.startswith("paths."):
                raise ValueError("paths.* 不允许运行时修改（会脱离现有数据目录）")
            if key_path == "threads" or key_path.startswith("threads."):
                raise ValueError("threads.* 不允许运行时修改（链路文件路径固定）")
            if key_path == "embedding" or key_path.startswith("embedding."):
                raise ValueError("embedding.* 不允许运行时修改（换模型/维度需用 migrate_bge.py 重建向量）")
            parts = key_path.split(".")
            node: Any = self.cfg
            for part in parts[:-1]:
                if not isinstance(node, dict) or part not in node:
                    raise KeyError(f"配置路径不存在: {key_path}")
                node = node[part]
            if not isinstance(node, dict) or parts[-1] not in node:
                raise KeyError(f"配置路径不存在: {key_path}")
            old = node[parts[-1]]
            node[parts[-1]] = value

            # 原子写回配置文件（合并后的完整配置），重启后保持
            tmp = self.config_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.config_path)
            self._cfg_mtime = self._stat_mtime()

            # 容量阈值变化 -> 立即治理（让"调小容量"当场生效）
            if key_path == "memory.capacity_threshold":
                await self._capacity_governance()
            return {"key_path": key_path, "old": old, "new": value}

    @staticmethod
    def _default_of(key_path: str) -> Any:
        """取 DEFAULT_CONFIG 里某点分路径的默认值（不存在则 KeyError）。"""
        node: Any = DEFAULT_CONFIG
        for part in key_path.split("."):
            if not isinstance(node, dict) or part not in node:
                raise KeyError(f"默认配置路径不存在: {key_path}")
            node = node[part]
        return json.loads(json.dumps(node))  # 深拷贝，防止默认值被改

    async def config_reset(self, key_path: Optional[str] = None) -> Dict[str, Any]:
        """恢复默认配置（**不清空任何缓存文件**，记忆数据原样保留）。

        - key_path 缺省：把 memory / retrieval / heat / storage / parallelism / reload
          整段重置为 DEFAULT_CONFIG 默认值；
        - key_path 指定：只重置该段/键（如 memory.capacity_threshold）；
        - paths.* / embedding.* / threads.* 永不重置（防止脱离数据目录、
          向量维度与模型不匹配）；重置后原子写回配置文件并触发一次容量治理。
        """
        async with self._get_lock():
            self._maybe_reload_config()
            protected = ("paths", "embedding", "threads")
            if key_path:
                top = key_path.split(".")[0]
                if top in protected:
                    raise ValueError(f"{top}.* 受保护，不允许重置")
                # 注意：不能在锁内调用 self.config_get（会重入 asyncio.Lock 死锁），直接遍历
                parts = key_path.split(".")
                node: Any = self.cfg
                for part in parts[:-1]:
                    if not isinstance(node, dict) or part not in node:
                        raise KeyError(f"配置路径不存在: {key_path}")
                    node = node[part]
                if not isinstance(node, dict) or parts[-1] not in node:
                    raise KeyError(f"配置路径不存在: {key_path}")
                old = node[parts[-1]]
                node[parts[-1]] = self._default_of(key_path)
                changed = {key_path: {"old": old, "new": node[parts[-1]]}}
            else:
                changed = {}
                for sec in ("memory", "retrieval", "heat", "storage", "parallelism", "reload"):
                    if sec in DEFAULT_CONFIG and sec in self.cfg:
                        old = json.loads(json.dumps(self.cfg[sec]))
                        self.cfg[sec] = self._default_of(sec)
                        if old != self.cfg[sec]:
                            changed[sec] = {"old": old, "new": self._default_of(sec)}

            # 原子写回配置文件，重启后保持
            tmp = self.config_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.config_path)
            self._cfg_mtime = self._stat_mtime()
            await self._capacity_governance()
            return {
                "reset": True,
                "scope": key_path or "memory/retrieval/heat/storage/parallelism/reload",
                "changed": changed,
                "memory_data_untouched": True,
            }

    async def set_paths(self, paths: Dict[str, str]) -> Dict[str, Any]:
        """用户自定义缓存路径（GUI 设置页高级区）：校验 + 迁移旧文件 + 写回配置。

        - 只接受 warm_cache / cold_archive / vector_store / vector_index / threads；
        - 相对路径按"配置所在目录"解析（与桥/MCP 的 CWD 锚定一致，绝不硬编码）；
        - 旧文件存在且新位置不存在 → os.replace 迁移（数据不丢）；新位置已存在则拒绝（不覆盖）；
        - 运行中的珊瑚进程（MCP）需重启后完全切换（内存态与 _VectorStore 路径不热切换）。
        """
        allowed = {"warm_cache", "cold_archive", "vector_store", "vector_index", "threads"}
        bad = set(paths) - allowed
        if bad:
            raise ValueError(f"不支持的路径键: {sorted(bad)}")
        async with self._get_lock():
            self._maybe_reload_config()
            base = os.path.dirname(os.path.abspath(self.config_path))  # 锚点：配置所在目录
            old_attrs = {
                "warm_cache": self.path_warm,
                "cold_archive": self.path_cold,
                "vector_store": self.path_vectors,
                "vector_index": self.path_vector_index,
                "threads": self.path_threads,
            }
            moved = []
            new_cfg_paths = dict(self.cfg.get("paths", {}))
            new_threads_path = self.cfg.get("threads", {}).get("path", "memory_data/coral_threads.json")
            for key, raw in paths.items():
                if not raw or not isinstance(raw, str):
                    raise ValueError(f"{key} 的路径为空或非法")
                new = os.path.abspath(os.path.join(base, raw))
                old = os.path.abspath(old_attrs[key])
                if old == new:
                    continue
                if os.path.exists(old):
                    if os.path.exists(new):
                        raise ValueError(f"新路径已存在文件，拒绝覆盖: {new}")
                    os.makedirs(os.path.dirname(new) or base, exist_ok=True)
                    os.replace(old, new)
                    moved.append({"key": key, "from": old, "to": new})
                if key == "threads":
                    new_threads_path = new
                else:
                    new_cfg_paths[key] = new
            # 写回配置（原子）
            self.cfg["paths"] = new_cfg_paths
            self.cfg.setdefault("threads", {})["path"] = new_threads_path
            tmp = self.config_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.config_path)
            self._cfg_mtime = self._stat_mtime()
            return {
                "ok": True,
                "moved": moved,
                "restart_required": True,
                "note": "路径已更新并迁移；运行中的珊瑚进程重启后完全切换",
            }

    async def report(self) -> Dict[str, Any]:
        """热度/占用审计报告（GUI 窗口A 的数据源）：
        stats + 磁盘明细 + 按天分布（近 14 天）+ 淘汰预警 Top5（最低热度）+ 文件路径。"""
        async with self._get_lock():
            self._maybe_reload_config()
            s = self.stats()
            d = self.disk_usage()
            cold_items = await self._read_all_cold()
            all_items = self.hot_memory + self.warm_memory + cold_items

            # 按天分布（近 14 天 + 更早）
            from datetime import datetime, timedelta

            today = datetime.now()
            days = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(13, -1, -1)]
            buckets = {day: 0 for day in days}
            earlier = 0
            for m in all_items:
                day = datetime.fromtimestamp(m.timestamp).strftime("%Y-%m-%d")
                if day in buckets:
                    buckets[day] += 1
                else:
                    earlier += 1

            # 淘汰预警 Top5：池内按最大访问数归一化的最低热度
            pool_max = max((m.access_count for m in all_items), default=0)
            def _loc(m: MemoryItem) -> str:
                if m in self.hot_memory:
                    return "hot"
                if m in self.warm_memory:
                    return "warm"
                return "cold"

            coldest = sorted(all_items, key=lambda m: self._heat_score(m, pool_max))[:5]
            eviction_preview = [
                {
                    "id": m.item_id,
                    "content": (m.content[:60] + "…") if len(m.content) > 60 else m.content,
                    "heat": round(self._heat_score(m, pool_max), 4),
                    "access_count": m.access_count,
                    "importance": m.importance,
                    "days_old": round((time.time() - m.timestamp) / 86400.0, 1),
                    "location": _loc(m),
                }
                for m in coldest
            ]
            return {
                "stats": s,
                "disk_usage": d,
                "day_histogram": {"last_14_days": buckets, "earlier": earlier, "total": len(all_items)},
                "eviction_preview": eviction_preview,
                "paths": {
                    "warm": os.path.abspath(self.path_warm),
                    "cold": os.path.abspath(self.path_cold),
                    "vectors": os.path.abspath(self.path_vectors),
                    "vector_index": os.path.abspath(self.path_vector_index),
                    "threads": os.path.abspath(self.path_threads),
                    "config": os.path.abspath(self.config_path),
                },
            }

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
            "threads": len(self._threads),   # 推理线索链路（永不遗忘，独立于记忆池）
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


@register_tool(
    name="memory_delete",
    description="按 item_id 删除一条记忆 / Delete a memory by item_id："
                "从热/温/冷区与向量库彻底移除（清理错记/测试残留）。"
                "item_id 可从 memory_search / coral_report 的 eviction_preview 获取。",
    parameters={
        "item_id": {"type": "string", "required": True, "description": "要删除的记忆 item_id"},
    },
)
async def memory_delete(item_id: str) -> Dict[str, Any]:
    """删除一条记忆（热/温/冷 + 向量）。"""
    coral = get_coral()
    removed = await coral.delete(item_id)
    return {"ok": removed, "item_id": item_id, "message": "deleted" if removed else "not found"}


# ---------------------------------------------------------------------------
# 推理线索链路（Thread）工具 —— 跨聊天协作：聊天 A 定宏观路径，B~F 各自推进
# 链路永不遗忘（独立存储、不参与热度淘汰/容量治理/磁盘配额）。
# ---------------------------------------------------------------------------
@register_tool(
    name="thread_create",
    description="创建推理线索链路（永不遗忘，不参与热度淘汰）Create a never-forgotten reasoning thread："
                "聊天 A 用它定宏观路径（标题 + 几句话摘要），其它聊天（B~F）都能看到并各自推进。"
                "链路存独立文件 memory_data/coral_threads.json，跨会话/跨聊天共享。",
    parameters={
        "title": {"type": "string", "required": True, "description": "链路名（短短语）"},
        "summary": {"type": "string", "required": False, "description": "宏观路径/当前状态（几句话）"},
        "parent_thread_id": {"type": "string", "required": False, "description": "父链路 ID（把链路串成线索链）"},
        "by": {"type": "string", "required": False, "description": "创建者标识（如 聊天A）"},
    },
)
async def thread_create(
    title: str,
    summary: str = "",
    parent_thread_id: Optional[str] = None,
    by: str = "",
) -> Dict[str, Any]:
    coral = get_coral()
    try:
        t = await coral.thread_create(title, summary=summary, parent_thread_id=parent_thread_id, by=by)
    except (ValueError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "thread": coral._thread_view(t)}


@register_tool(
    name="thread_status",
    description="查看推理线索链路状态 View reasoning thread status："
                "不带 thread_id 返回全部活跃链路总览（新聊天一进来就知道'到哪了'，跨聊天协作的宏观视图）；"
                "带 thread_id 返回该链路详情（含步骤推进链）；query 按标题/摘要关键词过滤。",
    parameters={
        "thread_id": {"type": "string", "required": False, "description": "指定链路 ID；缺省=全部（默认不含已归档）"},
        "include_archived": {"type": "boolean", "required": False, "description": "是否包含已归档链路，默认 false"},
        "query": {"type": "string", "required": False, "description": "按标题/摘要关键词过滤"},
    },
)
async def thread_status(
    thread_id: Optional[str] = None,
    include_archived: bool = False,
    query: Optional[str] = None,
) -> Dict[str, Any]:
    coral = get_coral()
    threads = await coral.thread_status(
        thread_id=thread_id, include_archived=include_archived, query=query
    )
    views = [coral._thread_view(t) for t in threads]
    if thread_id is not None:
        return {"ok": True, "count": len(views), "thread": views[0] if views else None}
    # 总览：标注子链路（线索链树）
    ids = {v["thread_id"] for v in views}
    for v in views:
        v["children"] = [w["thread_id"] for w in views if w.get("parent_thread_id") == v["thread_id"]]
    return {"ok": True, "count": len(views), "threads": views}


@register_tool(
    name="thread_advance",
    description="推进一条推理线索链路 Advance a reasoning thread："
                "追加一个步骤节点（谁在何时推进了什么），并更新链路的最近推进者。"
                "聊天 B~F 各自推进协作时用它；done=true 表示该步已完成。",
    parameters={
        "thread_id": {"type": "string", "required": True, "description": "要推进的链路 ID"},
        "note": {"type": "string", "required": True, "description": "本次推进的内容/步骤（短语即可）"},
        "done": {"type": "boolean", "required": False, "description": "是否标记该步完成，默认 false"},
        "by": {"type": "string", "required": False, "description": "推进者标识（如 聊天B）"},
    },
)
async def thread_advance(
    thread_id: str,
    note: str,
    done: bool = False,
    by: str = "",
) -> Dict[str, Any]:
    coral = get_coral()
    try:
        t = await coral.thread_advance(thread_id, note, done=done, by=by)
    except (ValueError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "thread": coral._thread_view(t)}


@register_tool(
    name="thread_interrupt",
    description="中断一条推理线索链路 Interrupt a reasoning thread："
                "状态 → interrupted，内容全部保留（永不遗忘），可随时 thread_resume 恢复。",
    parameters={
        "thread_id": {"type": "string", "required": True, "description": "要中断的链路 ID"},
        "reason": {"type": "string", "required": False, "description": "中断原因（会记入步骤链）"},
    },
)
async def thread_interrupt(thread_id: str, reason: str = "") -> Dict[str, Any]:
    coral = get_coral()
    try:
        t = await coral.thread_interrupt(thread_id, reason=reason)
    except (ValueError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "thread": coral._thread_view(t)}


@register_tool(
    name="thread_archive",
    description="归档一条推理线索链路 Archive a reasoning thread："
                "状态 → archived（不再出现在活跃总览，内容永不遗忘，可 thread_resume 恢复）。",
    parameters={
        "thread_id": {"type": "string", "required": True, "description": "要归档的链路 ID"},
    },
)
async def thread_archive(thread_id: str) -> Dict[str, Any]:
    coral = get_coral()
    try:
        t = await coral.thread_archive(thread_id)
    except (ValueError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "thread": coral._thread_view(t)}


@register_tool(
    name="thread_resume",
    description="恢复一条推理线索链路 Resume a reasoning thread："
                "把中断/归档的链路重新置为 active，继续推进。",
    parameters={
        "thread_id": {"type": "string", "required": True, "description": "要恢复的链路 ID"},
    },
)
async def thread_resume(thread_id: str) -> Dict[str, Any]:
    coral = get_coral()
    try:
        t = await coral.thread_resume(thread_id)
    except (ValueError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "thread": coral._thread_view(t)}


@register_tool(
    name="thread_link",
    description="把两条推理线索链路串成父子关系（线索链）Link two reasoning threads："
                "child 链路的 parent_thread_id 指向 parent，形成线索树。",
    parameters={
        "child_id": {"type": "string", "required": True, "description": "子链路 ID"},
        "parent_id": {"type": "string", "required": True, "description": "父链路 ID"},
    },
)
async def thread_link(child_id: str, parent_id: str) -> Dict[str, Any]:
    coral = get_coral()
    try:
        ok = await coral.thread_link(child_id, parent_id)
    except (ValueError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": ok}


@register_tool(
    name="coral_stats",
    description="查看脑珊瑚运行状态 View Coral stats："
                "热/温/冷记忆池占用、线程数、嵌入器、磁盘占用与配额比例 —— 管理上下文缓存的体检入口。",
    parameters={},
)
async def coral_stats() -> Dict[str, Any]:
    coral = get_coral()
    s = coral.stats()
    try:
        d = coral.disk_usage()
    except Exception:  # noqa: BLE001
        d = {}
    return {
        "ok": True,
        "stats": s,
        "disk_usage": d,
        "hint": "调优配置用 coral_config_get / coral_config_set",
    }


@register_tool(
    name="coral_config_get",
    description="查看脑珊瑚配置 View Coral config："
                "支持点分路径（如 memory / retrieval.top_k / storage.max_bytes），"
                "缺省返回全量配置 —— 管理上下文缓存（容量/阈值/top_k/配额）前先看这里。",
    parameters={
        "path": {"type": "string", "required": False, "description": "点分路径（如 memory.capacity_threshold）；缺省=全量"},
    },
)
async def coral_config_get(path: Optional[str] = None) -> Dict[str, Any]:
    coral = get_coral()
    try:
        value = await coral.config_get(path)
    except KeyError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "path": path, "config": _to_jsonable(value)}


@register_tool(
    name="coral_config_set",
    description="修改脑珊瑚配置 Set Coral config（管理上下文缓存的核心入口）："
                "热加载即时生效 + 原子写回 coral_config.json 持久化。"
                "常用：memory.capacity_threshold（记忆容量上限，改完立即治理）、"
                "retrieval.top_k（检索条数）、retrieval.min_score、storage.max_bytes（磁盘配额）。"
                "禁止修改 paths.* / embedding.* / threads.*（换模型请用 migrate_bge.py）。",
    parameters={
        "key_path": {"type": "string", "required": True, "description": "点分路径，如 memory.capacity_threshold"},
        "value": {"type": ["string", "number", "boolean"], "required": True, "description": "新值"},
    },
)
async def coral_config_set(key_path: str, value: Any) -> Dict[str, Any]:
    coral = get_coral()
    try:
        r = await coral.config_set(key_path, value)
    except (ValueError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "key_path": r["key_path"], "old": _to_jsonable(r["old"]), "new": _to_jsonable(r["new"])}


@register_tool(
    name="coral_config_reset",
    description="恢复脑珊瑚默认配置 Reset Coral config to defaults（**不清空任何缓存文件**，记忆数据原样保留）："
                "把 memory/retrieval/heat/storage/parallelism/reload 整段（或指定 key_path 单段/键）重置为默认值，"
                "原子写回 coral_config.json 并触发一次容量治理；paths.* / embedding.* / threads.* 受保护永不重置。",
    parameters={
        "key_path": {"type": "string", "required": False, "description": "只重置指定段/键（如 memory.capacity_threshold）；缺省=全部可重置段"},
    },
)
async def coral_config_reset(key_path: Optional[str] = None) -> Dict[str, Any]:
    coral = get_coral()
    try:
        r = await coral.config_reset(key_path)
    except (ValueError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, **r}


@register_tool(
    name="coral_report",
    description="脑珊瑚热度/占用审计报告 Coral health report："
                "stats + 磁盘明细 + 按天分布（近 14 天）+ 淘汰预警 Top5（最低热度冷记忆）+ 缓存文件路径 —— GUI 统计窗口的数据源。",
    parameters={},
)
async def coral_report() -> Dict[str, Any]:
    coral = get_coral()
    try:
        r = await coral.report()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, **r}


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
