/**
 * Coral board capsule, browser half: mounts the board Remote contribution
 * and registers the bottom-right capsule into `shell.overlay`. Pure UI and
 * session-free — rows arrive via `ctx.remote.board.list()`, so the model
 * never sees thread data and zero tokens are spent on the read path.
 * @module @deepseek-ai/dsh-client-coral-board/src/client
 */
import { BoardCapsule } from "./BoardCapsule.js";
import { TYPERT_REMOTE } from "./remote.js";
export { BoardCapsule } from "./BoardCapsule.js";
export { TYPERT_REMOTE } from "./remote.js";
/** Required services: slot registry + Remote carrier. `remote.board` is
 * self-mounted by this plugin's `$mount`, so it must NOT appear in inject. */
export const inject = ['slots', 'remote'];
/**
 * Client plugin body: mount the board namespace, then inject the capsule
 * into the shell overlay.
 * @param ctx - client root context.
 * @returns disposer retracting the Remote namespace.
 */
export async function apply(ctx) {
    const disposeRemote = await ctx.remote.$mount(TYPERT_REMOTE);
    // `ctx.get` bypasses the inject-guard; required because this fiber owns
    // the namespace it just mounted.
    const board = ctx.get('remote.board');
    ctx.slots.inject('shell.overlay', () => ctx.slots.register({
        name: 'shell.overlay',
        id: 'coral-board',
        order: 120,
        inject: () => ({ board }),
    }, BoardCapsule));
    return async () => { await disposeRemote(); };
}
//# sourceMappingURL=index.js.map