/**
 * API balance capsule, browser half: mounts the balance Remote contribution
 * and registers the bottom-right floating capsule into the frame's
 * `shell.overlay` list slot. The capsule is session-free (root scope) and
 * pure UI — balance data arrives through `ctx.remote.balance.*`, so the
 * model never sees it and no tokens are spent on the query path.
 * @module @deepseek-ai/dsh-client-ui-balance/src/client
 */
import { BalanceCapsule } from "./BalanceCapsule.js";
import { TYPERT_REMOTE } from "./remote.js";
export { BalanceCapsule } from "./BalanceCapsule.js";
export { TYPERT_REMOTE } from "./remote.js";
/** Required services: the slot registry and the Remote carrier. `remote.balance` is
 * self-mounted by this plugin's `$mount`, so it must NOT appear in inject — otherwise
 * the fiber waits for a service only its own `apply` can create (deadlock). */
export const inject = ['slots', 'remote'];
/**
 * Client plugin body: mount the balance namespace, then inject the capsule
 * into the shell overlay.
 * @param ctx - client root context.
 * @returns disposer retracting the Remote namespace.
 */
export async function apply(ctx) {
    const disposeRemote = await ctx.remote.$mount(TYPERT_REMOTE);
    // `ctx.get` bypasses the inject-guard (unlike `ctx.remote.balance`), which is
    // required here because this fiber owns the namespace it just mounted.
    const balance = ctx.get('remote.balance');
    ctx.slots.inject('shell.overlay', () => ctx.slots.register({
        name: 'shell.overlay',
        id: 'balance',
        order: 100,
        inject: () => ({ balance }),
    }, BalanceCapsule));
    return async () => { await disposeRemote(); };
}
//# sourceMappingURL=index.js.map