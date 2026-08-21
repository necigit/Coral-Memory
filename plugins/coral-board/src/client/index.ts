/**
 * Coral board capsule, browser half: mounts the board Remote contribution
 * and registers the bottom-right capsule into `shell.overlay`. Pure UI and
 * session-free — rows arrive via `ctx.remote.board.list()`, so the model
 * never sees thread data and zero tokens are spent on the read path.
 * @module @deepseek-ai/dsh-client-coral-board/src/client
 */

import type { ClientContext } from '@deepseek-ai/dsh-client-runtime/client'
// Type-only: pulls the ctx.remote merge (TypertClientRemote) into this program.
import type {} from '@deepseek-ai/dsh-api-remotes/client'
// Type-only: pulls the ui-layout SlotMap merge (the shell.overlay entry).
import type {} from '@deepseek-ai/dsh-client-ui-layout/client'
import { BoardCapsule } from './BoardCapsule.tsx'
import { TYPERT_REMOTE } from './remote.ts'
import type { BoardRemoteApi } from './remote.ts'

export { BoardCapsule } from './BoardCapsule.tsx'
export { TYPERT_REMOTE } from './remote.ts'
export type { BoardRemoteApi } from './remote.ts'

/** Required services: slot registry + Remote carrier. `remote.board` is
 * self-mounted by this plugin's `$mount`, so it must NOT appear in inject. */
export const inject = ['slots', 'remote']

/**
 * Client plugin body: mount the board namespace, then inject the capsule
 * into the shell overlay.
 * @param ctx - client root context.
 * @returns disposer retracting the Remote namespace.
 */
export async function apply(ctx: ClientContext): Promise<() => Promise<void>> {
  const disposeRemote = await ctx.remote.$mount(TYPERT_REMOTE)
  // `ctx.get` bypasses the inject-guard; required because this fiber owns
  // the namespace it just mounted.
  const board = ctx.get('remote.board') as BoardRemoteApi
  ctx.slots.inject('shell.overlay', () => ctx.slots.register({
    name: 'shell.overlay',
    id: 'coral-board',
    order: 120,
    inject: () => ({ board }),
  }, BoardCapsule))
  return async () => { await disposeRemote() }
}
