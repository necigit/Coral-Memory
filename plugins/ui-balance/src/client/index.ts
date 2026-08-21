/**
 * API balance capsule, browser half: mounts the balance Remote contribution
 * and registers the bottom-right floating capsule into the frame's
 * `shell.overlay` list slot. The capsule is session-free (root scope) and
 * pure UI — balance data arrives through `ctx.remote.balance.*`, so the
 * model never sees it and no tokens are spent on the query path.
 * @module @deepseek-ai/dsh-client-ui-balance/src/client
 */

import type { ClientContext } from '@deepseek-ai/dsh-client-runtime/client'
// Type-only: pulls the ctx.remote merge (TypertClientRemote) into this program.
import type {} from '@deepseek-ai/dsh-api-remotes/client'
// Type-only: pulls the ui-layout SlotMap merge (the shell.overlay entry).
import type {} from '@deepseek-ai/dsh-client-ui-layout/client'
import { BalanceCapsule } from './BalanceCapsule.tsx'
import { TYPERT_REMOTE } from './remote.ts'
import type { BalanceRemoteApi } from './remote.ts'

export { BalanceCapsule } from './BalanceCapsule.tsx'
export { TYPERT_REMOTE } from './remote.ts'
export type { BalanceRemoteApi } from './remote.ts'

/** Required services: the slot registry and the Remote carrier. `remote.balance` is
 * self-mounted by this plugin's `$mount`, so it must NOT appear in inject — otherwise
 * the fiber waits for a service only its own `apply` can create (deadlock). */
export const inject = ['slots', 'remote']

/**
 * Client plugin body: mount the balance namespace, then inject the capsule
 * into the shell overlay.
 * @param ctx - client root context.
 * @returns disposer retracting the Remote namespace.
 */
export async function apply(ctx: ClientContext): Promise<() => Promise<void>> {
  const disposeRemote = await ctx.remote.$mount(TYPERT_REMOTE)
  // `ctx.get` bypasses the inject-guard (unlike `ctx.remote.balance`), which is
  // required here because this fiber owns the namespace it just mounted.
  const balance = ctx.get('remote.balance') as BalanceRemoteApi
  ctx.slots.inject('shell.overlay', () => ctx.slots.register({
    name: 'shell.overlay',
    id: 'balance',
    order: 100,
    inject: () => ({ balance }),
  }, BalanceCapsule))
  return async () => { await disposeRemote() }
}
