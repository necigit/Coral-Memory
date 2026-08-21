/**
 * API balance capsule, node half. Hosts the `balance` Typert Remote service:
 * a persistent multi-API entry store ($DSH_HOME/balance.json) plus per-entry
 * balance queries over plain fetch. Keys never cross the Remote boundary —
 * list() strips inline keys, and resolution happens per query through the
 * credentials seam / environment. The browser half only ever receives
 * BalanceEntry (sans secrets) and BalanceResult payloads, so the capsule
 * costs zero model tokens: no agent, no context, no session involvement.
 * @module @deepseek-ai/dsh-client-ui-balance
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
import { randomUUID } from 'node:crypto';
import { Remote, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol';
import { loadStore, queryBalance, resolveApiKey, stripSecrets, writeStore, } from "./balance-store.js";
/** The `balance` Remote service: store CRUD + query, all session-free. */
let BalanceService = (() => {
    let _classSuper = TypertRemoteService;
    let _instanceExtraInitializers = [];
    let _list_decorators;
    let _query_decorators;
    let _add_decorators;
    let _remove_decorators;
    let _update_decorators;
    return class BalanceService extends _classSuper {
        static {
            const _metadata = typeof Symbol === "function" && Symbol.metadata ? Object.create(_classSuper[Symbol.metadata] ?? null) : void 0;
            _list_decorators = [Remote('list')];
            _query_decorators = [Remote('query')];
            _add_decorators = [Remote('add')];
            _remove_decorators = [Remote('remove')];
            _update_decorators = [Remote('update')];
            __esDecorate(this, null, _list_decorators, { kind: "method", name: "list", static: false, private: false, access: { has: obj => "list" in obj, get: obj => obj.list }, metadata: _metadata }, null, _instanceExtraInitializers);
            __esDecorate(this, null, _query_decorators, { kind: "method", name: "query", static: false, private: false, access: { has: obj => "query" in obj, get: obj => obj.query }, metadata: _metadata }, null, _instanceExtraInitializers);
            __esDecorate(this, null, _add_decorators, { kind: "method", name: "add", static: false, private: false, access: { has: obj => "add" in obj, get: obj => obj.add }, metadata: _metadata }, null, _instanceExtraInitializers);
            __esDecorate(this, null, _remove_decorators, { kind: "method", name: "remove", static: false, private: false, access: { has: obj => "remove" in obj, get: obj => obj.remove }, metadata: _metadata }, null, _instanceExtraInitializers);
            __esDecorate(this, null, _update_decorators, { kind: "method", name: "update", static: false, private: false, access: { has: obj => "update" in obj, get: obj => obj.update }, metadata: _metadata }, null, _instanceExtraInitializers);
            if (_metadata) Object.defineProperty(this, Symbol.metadata, { enumerable: true, configurable: true, writable: true, value: _metadata });
        }
        constructor(ctx) {
            super(ctx, 'balance');
            __runInitializers(this, _instanceExtraInitializers);
        }
        /** Optional credential seam; absent in bare contexts (env fallback still applies). */
        credentials() {
            return this.ctx.get('credentials');
        }
        /** Current entries, seeding the default DeepSeek row on first run. */
        async load() {
            return await loadStore(this.credentials());
        }
        /** List configured entries; inline keys are never echoed. */
        async list() {
            return (await this.load()).map(stripSecrets);
        }
        /** Query every configured entry and return per-entry outcomes. */
        async query() {
            const entries = await this.load();
            const credentials = this.credentials();
            return await Promise.all(entries.map(async (entry) => {
                const key = await resolveApiKey(entry, credentials);
                return await queryBalance(entry, key);
            }));
        }
        /** Add one API entry and return the fresh (secrets-stripped) list. */
        async add(entry) {
            const label = entry.label.trim();
            if (label.length === 0)
                throw new Error('balance: label 不能为空');
            if (entry.kind !== 'deepseek' && entry.kind !== 'custom') {
                throw new Error('balance: kind 必须是 deepseek 或 custom');
            }
            const apis = await this.load();
            const created = { ...entry, label, id: `api-${randomUUID().slice(0, 8)}` };
            apis.push(created);
            await writeStore({ apis });
            return apis.map(stripSecrets);
        }
        /** Remove one API entry and return the fresh list. */
        async remove(id) {
            const apis = await this.load();
            const next = apis.filter(entry => entry.id !== id);
            if (next.length === apis.length)
                throw new Error(`balance: 未知条目 "${id}"`);
            await writeStore({ apis: next });
            return next.map(stripSecrets);
        }
        /** Patch one API entry and return the fresh list. */
        async update(id, patch) {
            const apis = await this.load();
            const at = apis.findIndex(entry => entry.id === id);
            if (at < 0)
                throw new Error(`balance: 未知条目 "${id}"`);
            const current = apis[at];
            if (current === undefined)
                throw new Error(`balance: 未知条目 "${id}"`);
            const merged = {
                id: current.id,
                label: patch.label ?? current.label,
                kind: patch.kind ?? current.kind,
            };
            if (patch.baseUrl !== undefined)
                merged.baseUrl = patch.baseUrl;
            else if (current.baseUrl !== undefined)
                merged.baseUrl = current.baseUrl;
            if (patch.balanceUrl !== undefined)
                merged.balanceUrl = patch.balanceUrl;
            else if (current.balanceUrl !== undefined)
                merged.balanceUrl = current.balanceUrl;
            if (patch.apiKey !== undefined)
                merged.apiKey = patch.apiKey;
            else if (current.apiKey !== undefined)
                merged.apiKey = current.apiKey;
            if (patch.credential !== undefined)
                merged.credential = patch.credential;
            else if (current.credential !== undefined)
                merged.credential = current.credential;
            if (merged.label.trim().length === 0)
                throw new Error('balance: label 不能为空');
            apis[at] = merged;
            await writeStore({ apis });
            return apis.map(stripSecrets);
        }
    };
})();
export { BalanceService };
/** Host plugin body: mount the service. */
export function apply(ctx) {
    ctx.plugin(BalanceService);
}
export default BalanceService;
//# sourceMappingURL=index.js.map