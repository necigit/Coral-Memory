import { randomUUID } from "node:crypto";
import { Remote, TypertRemoteService } from "@deepseek-ai/dsh-typert-protocol";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { credentialRef } from "@deepseek-ai/dsh-credentials";
/** Per-query network timeout. */
const QUERY_TIMEOUT_MS = 1e4;
/** Credential name seeded into the store on first run. */
const DEFAULT_CREDENTIAL = "DEEPSEEK_API_KEY";
/** Store file id for the seeded default entry. */
const DEFAULT_ENTRY_ID = "deepseek-default";
/** Resolve the persistent store file (overridable for tests). */
function balanceStorePath(file = join(process.env.DSH_HOME ?? join(homedir(), ".dsh"), "balance.json")) {
	return file;
}
/** Read the store; returns null when absent or malformed. */
async function readStore(file = balanceStorePath()) {
	try {
		const raw = await readFile(file, "utf8");
		const parsed = JSON.parse(raw);
		if (!Array.isArray(parsed.apis)) return null;
		return { apis: parsed.apis };
	} catch {
		return null;
	}
}
/** Write the store, creating its directory when needed. */
async function writeStore(store, file = balanceStorePath()) {
	await mkdir(dirname(file), { recursive: true });
	await writeFile(file, JSON.stringify(store, null, 2), "utf8");
}
/**
* Resolve an entry's key: inline apiKey wins, then the named credential
* (env first, then the credentials seam).
* @param entry - stored entry.
* @param credentials - optional credentials service (absent in bare contexts).
* @returns the key, or undefined when nothing is configured.
*/
async function resolveApiKey(entry, credentials) {
	if (typeof entry.apiKey === "string" && entry.apiKey.length > 0) return entry.apiKey;
	if (typeof entry.credential === "string" && entry.credential.length > 0) {
		const viaEnv = process.env[entry.credential];
		if (typeof viaEnv === "string" && viaEnv.length > 0) return viaEnv;
		if (credentials !== void 0) {
			const resolved = await credentials.resolve(credentialRef(entry.credential));
			if (resolved !== void 0) return resolved.value;
		}
	}
}
/**
* Default seed: one deepseek entry bound to the DEEPSEEK_API_KEY credential,
* only when that credential actually resolves (so a fresh install with no key
* yields an empty store instead of a dead row).
* @param credentials - optional credentials service.
* @returns the seeded entries (empty when no key exists).
*/
async function seedDefault(credentials) {
	if (await resolveApiKey({
		id: DEFAULT_ENTRY_ID,
		label: "DeepSeek",
		kind: "deepseek",
		credential: DEFAULT_CREDENTIAL
	}, credentials) === void 0) return [];
	return [{
		id: DEFAULT_ENTRY_ID,
		label: "DeepSeek",
		kind: "deepseek",
		credential: DEFAULT_CREDENTIAL
	}];
}
/** Load the store, seeding defaults on first run (persisted only when a seed exists). */
async function loadStore(credentials, file = balanceStorePath()) {
	const existing = await readStore(file);
	if (existing !== null) return existing.apis;
	const seeded = await seedDefault(credentials);
	if (seeded.length > 0) await writeStore({ apis: seeded }, file);
	return seeded;
}
/** Strip inline keys before anything crosses the Remote boundary. */
function stripSecrets(entry) {
	if (entry.apiKey === void 0) return entry;
	const { apiKey: _stripped, ...rest } = entry;
	return rest;
}
/** Parse a provider balance response body into currency buckets. */
function parseBalanceInfos(body) {
	const infos = body["balance_infos"];
	if (Array.isArray(infos) && infos.length > 0) {
		const buckets = [];
		for (const raw of infos) {
			if (typeof raw !== "object" || raw === null) continue;
			const record = raw;
			const total = record["total_balance"];
			const currency = record["currency"];
			if (typeof total !== "string" && typeof total !== "number") continue;
			buckets.push({
				currency: typeof currency === "string" && currency.length > 0 ? currency : "?",
				total: String(total)
			});
		}
		if (buckets.length > 0) return buckets;
	}
	const direct = body["balance"];
	if (typeof direct === "number" || typeof direct === "string" && direct.length > 0) return [{
		currency: "?",
		total: String(direct)
	}];
	const data = typeof body["data"] === "object" && body["data"] !== null ? body["data"] : void 0;
	const limits = data?.["limits"];
	if (Array.isArray(limits) && limits.length > 0) {
		const buckets = [];
		for (const raw of limits) {
			if (typeof raw !== "object" || raw === null) continue;
			const record = raw;
			const remaining = record["remaining"];
			const total = record["number"];
			if (typeof remaining !== "number" && typeof remaining !== "string") continue;
			if (typeof total !== "number" && typeof total !== "string") continue;
			const label = typeof record["type"] === "string" && record["type"].length > 0 ? record["type"] : "配额";
			buckets.push({
				currency: label,
				total: `${String(remaining)}/${String(total)}`
			});
		}
		if (buckets.length > 0) return buckets;
	}
	const nested = data?.["balance"];
	if (typeof nested === "number" || typeof nested === "string" && nested.length > 0) return [{
		currency: "?",
		total: String(nested)
	}];
}
/**
* Query one entry and reduce the outcome to a BalanceResult. Never throws:
* every failure becomes an !ok result so one broken API cannot kill the list.
* @param entry - stored entry.
* @param key - resolved API key (undefined → configuration error).
* @returns the per-entry outcome.
*/
async function queryBalance(entry, key) {
	const updatedAt = Date.now();
	const base = {
		id: entry.id,
		label: entry.label,
		updatedAt
	};
	if (key === void 0) return {
		...base,
		ok: false,
		error: "未配置 API key（填 apiKey 或 credential）"
	};
	let url;
	if (entry.kind === "deepseek") url = `${entry.baseUrl ?? "https://api.deepseek.com"}/user/balance`;
	else {
		url = entry.balanceUrl ?? "";
		if (url.length === 0) return {
			...base,
			ok: false,
			error: "自定义接口需要 balanceUrl"
		};
	}
	try {
		const response = await fetch(url, {
			headers: { Authorization: `Bearer ${key}` },
			signal: AbortSignal.timeout(QUERY_TIMEOUT_MS)
		});
		if (response.status === 401 || response.status === 403) return {
			...base,
			ok: false,
			error: `认证失败（HTTP ${response.status}）`
		};
		if (!response.ok) return {
			...base,
			ok: false,
			error: `HTTP ${response.status}`
		};
		const body = await response.json();
		const bodyCode = body["code"];
		const bodyMsg = body["msg"];
		if (body["success"] === false || typeof bodyCode === "number" && bodyCode !== 200) {
			const reason = typeof bodyMsg === "string" && bodyMsg.length > 0 ? bodyMsg : `provider error（code ${String(bodyCode)}）`;
			return {
				...base,
				ok: false,
				error: reason
			};
		}
		const infos = parseBalanceInfos(body);
		if (infos === void 0) return {
			...base,
			ok: false,
			error: "无法解析余额响应"
		};
		return {
			...base,
			ok: true,
			available: body["is_available"] !== false,
			infos
		};
	} catch (error) {
		return {
			...base,
			ok: false,
			error: error instanceof Error ? error.message : String(error)
		};
	}
}
//#endregion
//#region lib/types/index.js
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
var __runInitializers = function(thisArg, initializers, value) {
	var useValue = arguments.length > 2;
	for (var i = 0; i < initializers.length; i++) value = useValue ? initializers[i].call(thisArg, value) : initializers[i].call(thisArg);
	return useValue ? value : void 0;
};
var __esDecorate = function(ctor, descriptorIn, decorators, contextIn, initializers, extraInitializers) {
	function accept(f) {
		if (f !== void 0 && typeof f !== "function") throw new TypeError("Function expected");
		return f;
	}
	var kind = contextIn.kind, key = kind === "getter" ? "get" : kind === "setter" ? "set" : "value";
	var target = !descriptorIn && ctor ? contextIn["static"] ? ctor : ctor.prototype : null;
	var descriptor = descriptorIn || (target ? Object.getOwnPropertyDescriptor(target, contextIn.name) : {});
	var _, done = false;
	for (var i = decorators.length - 1; i >= 0; i--) {
		var context = {};
		for (var p in contextIn) context[p] = p === "access" ? {} : contextIn[p];
		for (var p in contextIn.access) context.access[p] = contextIn.access[p];
		context.addInitializer = function(f) {
			if (done) throw new TypeError("Cannot add initializers after decoration has completed");
			extraInitializers.push(accept(f || null));
		};
		var result = (0, decorators[i])(kind === "accessor" ? {
			get: descriptor.get,
			set: descriptor.set
		} : descriptor[key], context);
		if (kind === "accessor") {
			if (result === void 0) continue;
			if (result === null || typeof result !== "object") throw new TypeError("Object expected");
			if (_ = accept(result.get)) descriptor.get = _;
			if (_ = accept(result.set)) descriptor.set = _;
			if (_ = accept(result.init)) initializers.unshift(_);
		} else if (_ = accept(result)) if (kind === "field") initializers.unshift(_);
		else descriptor[key] = _;
	}
	if (target) Object.defineProperty(target, contextIn.name, descriptor);
	done = true;
};
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
			_list_decorators = [Remote("list")];
			_query_decorators = [Remote("query")];
			_add_decorators = [Remote("add")];
			_remove_decorators = [Remote("remove")];
			_update_decorators = [Remote("update")];
			__esDecorate(this, null, _list_decorators, {
				kind: "method",
				name: "list",
				static: false,
				private: false,
				access: {
					has: (obj) => "list" in obj,
					get: (obj) => obj.list
				},
				metadata: _metadata
			}, null, _instanceExtraInitializers);
			__esDecorate(this, null, _query_decorators, {
				kind: "method",
				name: "query",
				static: false,
				private: false,
				access: {
					has: (obj) => "query" in obj,
					get: (obj) => obj.query
				},
				metadata: _metadata
			}, null, _instanceExtraInitializers);
			__esDecorate(this, null, _add_decorators, {
				kind: "method",
				name: "add",
				static: false,
				private: false,
				access: {
					has: (obj) => "add" in obj,
					get: (obj) => obj.add
				},
				metadata: _metadata
			}, null, _instanceExtraInitializers);
			__esDecorate(this, null, _remove_decorators, {
				kind: "method",
				name: "remove",
				static: false,
				private: false,
				access: {
					has: (obj) => "remove" in obj,
					get: (obj) => obj.remove
				},
				metadata: _metadata
			}, null, _instanceExtraInitializers);
			__esDecorate(this, null, _update_decorators, {
				kind: "method",
				name: "update",
				static: false,
				private: false,
				access: {
					has: (obj) => "update" in obj,
					get: (obj) => obj.update
				},
				metadata: _metadata
			}, null, _instanceExtraInitializers);
			if (_metadata) Object.defineProperty(this, Symbol.metadata, {
				enumerable: true,
				configurable: true,
				writable: true,
				value: _metadata
			});
		}
		constructor(ctx) {
			super(ctx, "balance");
			__runInitializers(this, _instanceExtraInitializers);
		}
		/** Optional credential seam; absent in bare contexts (env fallback still applies). */
		credentials() {
			return this.ctx.get("credentials");
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
				return await queryBalance(entry, await resolveApiKey(entry, credentials));
			}));
		}
		/** Add one API entry and return the fresh (secrets-stripped) list. */
		async add(entry) {
			const label = entry.label.trim();
			if (label.length === 0) throw new Error("balance: label 不能为空");
			if (entry.kind !== "deepseek" && entry.kind !== "custom") throw new Error("balance: kind 必须是 deepseek 或 custom");
			const apis = await this.load();
			const created = {
				...entry,
				label,
				id: `api-${randomUUID().slice(0, 8)}`
			};
			apis.push(created);
			await writeStore({ apis });
			return apis.map(stripSecrets);
		}
		/** Remove one API entry and return the fresh list. */
		async remove(id) {
			const apis = await this.load();
			const next = apis.filter((entry) => entry.id !== id);
			if (next.length === apis.length) throw new Error(`balance: 未知条目 "${id}"`);
			await writeStore({ apis: next });
			return next.map(stripSecrets);
		}
		/** Patch one API entry and return the fresh list. */
		async update(id, patch) {
			const apis = await this.load();
			const at = apis.findIndex((entry) => entry.id === id);
			if (at < 0) throw new Error(`balance: 未知条目 "${id}"`);
			const current = apis[at];
			if (current === void 0) throw new Error(`balance: 未知条目 "${id}"`);
			const merged = {
				id: current.id,
				label: patch.label ?? current.label,
				kind: patch.kind ?? current.kind
			};
			if (patch.baseUrl !== void 0) merged.baseUrl = patch.baseUrl;
			else if (current.baseUrl !== void 0) merged.baseUrl = current.baseUrl;
			if (patch.balanceUrl !== void 0) merged.balanceUrl = patch.balanceUrl;
			else if (current.balanceUrl !== void 0) merged.balanceUrl = current.balanceUrl;
			if (patch.apiKey !== void 0) merged.apiKey = patch.apiKey;
			else if (current.apiKey !== void 0) merged.apiKey = current.apiKey;
			if (patch.credential !== void 0) merged.credential = patch.credential;
			else if (current.credential !== void 0) merged.credential = current.credential;
			if (merged.label.trim().length === 0) throw new Error("balance: label 不能为空");
			apis[at] = merged;
			await writeStore({ apis });
			return apis.map(stripSecrets);
		}
	};
})();
/** Host plugin body: mount the service. */
function apply(ctx) {
	ctx.plugin(BalanceService);
}
//#endregion
export { BalanceService, BalanceService as default, apply };
