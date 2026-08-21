/**
 * Package-owned invariant companion for `@deepseek-ai/dsh-client-ui-balance`.
 * @module @deepseek-ai/dsh-client-ui-balance/invariant
 */
const PACKAGE_NAME = '@deepseek-ai/dsh-client-ui-balance';
/** Cordis companion plugin name. */
export const name = 'client-ui-balance-invariant';
/** Service required before the companion can reserve package ownership. */
export const inject = ['invariants'];
/**
 * No runtime invariant: the capsule owns no store (React state + the node
 * half's file store), emits no cordis events, and holds no cross-plugin
 * mutable state. Remote mutations are validated by the service itself.
 */
const install = () => { };
/**
 * Register this package's invariant companion.
 * @param ctx - Cordis context carrying the invariant service.
 * @returns the installed registration's disposer after setup succeeds.
 */
export const apply = (ctx) => Promise.resolve(ctx.invariants.register(PACKAGE_NAME, install));
/* jscpd:ignore-end */
//# sourceMappingURL=invariant.js.map