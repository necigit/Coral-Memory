/**
 * API balance capsule, browser half: mounts the balance Remote contribution
 * and registers the bottom-right floating capsule into the frame's
 * `shell.overlay` list slot. The capsule is session-free (root scope) and
 * pure UI — balance data arrives through `ctx.remote.balance.*`, so the
 * model never sees it and no tokens are spent on the query path.
 * @module @deepseek-ai/dsh-client-ui-balance/src/client
 */
import type { ClientContext } from '@deepseek-ai/dsh-client-runtime/client';
export { BalanceCapsule } from './BalanceCapsule.tsx';
export { TYPERT_REMOTE } from './remote.ts';
export type { BalanceRemoteApi } from './remote.ts';
/** Required services: the slot registry and the Remote carrier. `remote.balance` is
 * self-mounted by this plugin's `$mount`, so it must NOT appear in inject — otherwise
 * the fiber waits for a service only its own `apply` can create (deadlock). */
export declare const inject: string[];
/**
 * Client plugin body: mount the balance namespace, then inject the capsule
 * into the shell overlay.
 * @param ctx - client root context.
 * @returns disposer retracting the Remote namespace.
 */
export declare function apply(ctx: ClientContext): Promise<() => Promise<void>>;
//# sourceMappingURL=index.d.ts.map