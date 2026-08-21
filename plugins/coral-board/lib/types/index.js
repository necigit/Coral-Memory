/**
 * Coral reasoning-thread task board, node half. Hosts the `board` Typert
 * Remote service: a read-only projection of coral_threads.json (the coral
 * memory system's reasoning-thread store). No tokens are spent 鈥?the file
 * is read on the node side and only small rows travel to the browser.
 * Management (archiving etc.) intentionally stays with the LLM via the
 * coral MCP tools; the capsule is a watch-only board.
 *
 * Threads file location: env CORAL_THREADS_PATH > $DSH_HOME/coral_board.json
 * {threadsPath} > the author's default below.
 * @module @deepseek-ai/dsh-client-coral-board
 */
var __runInitializers = (this && this.__runInitializers) || function (thisArg, initializers, value) {
    var useValue = arguments.length > 2;
    for (var i = 0; i < initializers.length; i++) {
        value = useValue ? initializers[i].call(thisArg, value) : initializers[i].call(thisArg);
    }
    return useValue ? value : void 0;
};
var __esDecorate = (this && this.__esDecorate) || function (ctor, descriptorIn, decorators, contextIn, initializers, extraInitializers) {
    function accept(f) { if (f !== void 0 && typeof f !== "function") throw new TypeError("Function expected"); return f; }
    var kind = contextIn.kind, key = kind === "getter" ? "get" : kind === "setter" ? "set" : "value";
    var target = !descriptorIn && ctor ? contextIn["static"] ? ctor : ctor.prototype : null;
    var descriptor = descriptorIn || (target ? Object.getOwnPropertyDescriptor(target, contextIn.name) : {});
    var _, done = false;
    for (var i = decorators.length - 1; i >= 0; i--) {
        var context = {};
        for (var p in contextIn) context[p] = p === "access" ? {} : contextIn[p];
        for (var p in contextIn.access) context.access[p] = contextIn.access[p];
        context.addInitializer = function (f) { if (done) throw new TypeError("Cannot add initializers after decoration has completed"); extraInitializers.push(accept(f || null)); };
        var result = (0, decorators[i])(kind === "accessor" ? { get: descriptor.get, set: descriptor.set } : descriptor[key], context);
        if (kind === "accessor") {
            if (result === void 0) continue;
            if (result === null || typeof result !== "object") throw new TypeError("Object expected");
            if (_ = accept(result.get)) descriptor.get = _;
            if (_ = accept(result.set)) descriptor.set = _;
            if (_ = accept(result.init)) initializers.unshift(_);
        }
        else if (_ = accept(result)) {
            if (kind === "field") initializers.unshift(_);
            else descriptor[key] = _;
        }
    }
    if (target) Object.defineProperty(target, contextIn.name, descriptor);
    done = true;
};
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Remote, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol';
/** 鍚﹀畾/璁″垝璇?鈥斺€?姝ラ鏂囨湰鍚叾涓€鍗宠涓恒€屾湭瀹屾垚銆嶏紝鍗充娇鍚屾椂甯﹀畬鎴愯瘝
 * 锛堝 "璋冪爺瀹屾垚锛屼笅涓€姝ヨ仈璋? / "宸蹭笂绾匡紝寰呴獙璇? / "璁″垝鍙戝竷"锛夈€?*/
const PENDING_RE = /(?:寰厊鏈獆灏殀杩橀渶|浠嶉渶|璁″垝|鍑嗗|鎷焲鎵撶畻|涓嬩竴姝寰呭姙|灏氭湭|鏆傛湭|鍏堜笉)/;
/** 瀹屾垚璇?鈥斺€?瀹屾垚/浜や粯/涓婄嚎/鍙戝竷/鏀跺畼/楠屾敹/绔ｅ伐/瀹屽伐/鏀跺熬/鏀跺伐/瀹屾瘯/鎼炲畾/
 * 钀藉湴/瑙ｅ喅/瀹氱/鎺ㄩ€侊紝鍏佽 宸?鍏ㄩ儴/閮?鍓嶇紑涓?鉁呪湏鉁旓紒!銆?鍚庣紑銆? * 涓庣強鐟氬啓绔?three_dog_coral.py 鐨?_STEP_COMPLETED_RE 淇濇寔涓€鑷淬€?*/
const COMPLETED_RE = /(?:宸瞸鍏ㄩ儴|閮絴宸插叏閮??(?:瀹屾垚|浜や粯|涓婄嚎|鍙戝竷|鏀跺畼|楠屾敹|绔ｅ伐|瀹屽伐|鏀跺熬|鏀跺伐|瀹屾瘯|鎼炲畾|钀藉湴|瑙ｅ喅|瀹氱|鎺ㄩ€?(?:鉁厊鉁搢鉁攟锛亅!|銆??/;
/** 涓€姝ユ槸鍚﹁璧锋潵鍍忓凡瀹屾垚锛氭樉寮?done=true 浼樺厛锛涘惁鍒欐寜瀹屾垚璇嶅垽瀹氾紝
 * 鍛戒腑鍚﹀畾/璁″垝璇嶅嵆涓嶇畻銆傛棫鏁版嵁鍑犱箮浠庝笉鍐?done=true锛屽叏闈犺繖鏉″惎鍙戝紡銆?*/
function stepLooksDone(step) {
    if (step.done === true)
        return true;
    const text = String(step.text ?? '').trim();
    if (text.length === 0)
        return false;
    if (PENDING_RE.test(text))
        return false;
    return COMPLETED_RE.test(text);
}
/** Resolve the coral_threads.json path (env > config file > default). */
function threadsPath() {
    if (process.env.CORAL_THREADS_PATH)
        return process.env.CORAL_THREADS_PATH;
    try {
        const home = process.env.DSH_HOME ?? os.homedir();
        const cfg = JSON.parse(fs.readFileSync(path.join(home, 'coral_board.json'), 'utf-8'));
        if (typeof cfg.threadsPath === 'string' && cfg.threadsPath.length > 0)
            return cfg.threadsPath;
    }
    catch {
        // config absent or unreadable 鈫?fall through to default
    }
    return path.join(process.env.DSH_HOME ?? os.homedir(), 'coral_threads.json');
}
/** The `board` Remote service: read-only thread board projection. */
let BoardService = (() => {
    let _classSuper = TypertRemoteService;
    let _instanceExtraInitializers = [];
    let _list_decorators;
    return class BoardService extends _classSuper {
        static {
            const _metadata = typeof Symbol === "function" && Symbol.metadata ? Object.create(_classSuper[Symbol.metadata] ?? null) : void 0;
            _list_decorators = [Remote('list')];
            __esDecorate(this, null, _list_decorators, { kind: "method", name: "list", static: false, private: false, access: { has: obj => "list" in obj, get: obj => obj.list }, metadata: _metadata }, null, _instanceExtraInitializers);
            if (_metadata) Object.defineProperty(this, Symbol.metadata, { enumerable: true, configurable: true, writable: true, value: _metadata });
        }
        constructor(ctx) {
            super(ctx, 'board');
            __runInitializers(this, _instanceExtraInitializers);
        }
        /** List all threads with projected board rows (active first, newest first). */
        async list() {
            const file = threadsPath();
            let raw;
            try {
                raw = JSON.parse(fs.readFileSync(file, 'utf-8'));
            }
            catch (error) {
                throw new Error(`board: 璇诲彇绾跨▼鏂囦欢澶辫触 ${file} 鈥?${String(error)}`);
            }
            const list = Array.isArray(raw)
                ? raw
                : raw !== null && typeof raw === 'object'
                    ? Object.values(raw)
                    : [];
            const now = Date.now();
            const rows = list.map((t) => {
                const th = t;
                const steps = Array.isArray(th.steps) ? th.steps : [];
                const doneSteps = steps.filter(stepLooksDone).length;
                const updatedAt = typeof th.updated_at === 'number' ? Math.round(th.updated_at * 1000) : now;
                const status = String(th.status ?? '?');
                // 瑙﹀彂閽╁瓙锛堝綊妗ｅ€欓€夛級锛氬彧鐪嬨€屾渶鏂颁竴姝ャ€嶆槸鍚﹁璧锋潵鍍忓畬宸ワ紝鎴栧叏閮ㄦ楠ら兘瀹屾垚銆?                // 涓嶅啀鎷?summary 鍏ㄥ眬鎵叧閿瘝 鈥斺€?鎽樿閲?"璁″垝涓婄嚎/寰呭彂甯? 涔嬬被鐨勮鍒掕瘝浼氳瑙﹀彂銆?                const lastStep = steps[steps.length - 1];
                const allDone = steps.length > 0 && doneSteps === steps.length;
                const lastDone = lastStep !== undefined && stepLooksDone(lastStep);
                const isCandidate = status === 'active' && steps.length > 0 && (allDone || lastDone);
                return {
                    id: String(th.thread_id ?? ''),
                    title: String(th.title ?? '(鏃犳爣棰?'),
                    summary: String(th.summary ?? ''),
                    status,
                    steps: steps.length,
                    doneSteps,
                    lastAdvanceBy: String(th.last_advance_by ?? ''),
                    updatedAt,
                    ageDays: Math.round(((now - updatedAt) / 86_400_000) * 10) / 10,
                    isCandidate,
                };
            });
            const order = { active: 0, interrupted: 1, archived: 2 };
            return rows.sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9)
                || b.updatedAt - a.updatedAt);
        }
    };
})();
export { BoardService };
/** Host plugin body: mount the service. */
export function apply(ctx) {
    ctx.plugin(BoardService);
}
export default BoardService;
//# sourceMappingURL=index.js.map