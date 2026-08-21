/**
 * Coral board capsule, browser half: mounts the board Remote contribution
 * and registers the bottom-right capsule into `shell.overlay`. Pure UI and
 * session-free — rows arrive via `ctx.remote.board.list()`, so the model
 * never sees thread data and zero tokens are spent on the read path.
 * @module @deepseek-ai/dsh-client-coral-board/src/client
 */
import type { ClientContext } from '@deepseek-ai/dsh-client-runtime/client';
export { BoardCapsule } from './BoardCapsule.tsx';
export { TYPERT_REMOTE } from './remote.ts';
export type { BoardRemoteApi } from './remote.ts';
/** Required services: slot registry + Remote carrier. `remote.board` is
 * self-mounted by this plugin's `$mount`, so it must NOT appear in inject. */
export declare const inject: string[];
/**
 * Client plugin body: mount the board namespace, then inject the capsule
 * into the shell overlay.
 * @param ctx - client root context.
 * @returns disposer retracting the Remote namespace.
 */
export declare function apply(ctx: ClientContext): Promise<() => Promise<void>>;
//# sourceMappingURL=index.d.ts.map