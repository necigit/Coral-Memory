/**
 * BalanceCapsule: a bottom-right vertical stack of pills — ONE pill per
 * configured API, each showing its own balance/status. Management (add /
 * remove) is intentionally NOT in the UI: the user drives it through the
 * LLM, which mutates the store via the balance service / management script.
 * Clicking an entry pill opens a small read-only detail panel (full
 * error/balance text). Session-free root scope — local React state fed by
 * `ctx.remote.balance.*`, refreshed on a visible-only timer.
 */
import type { PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots';
import type { BalanceRemoteApi } from './remote.ts';
export type BalanceCapsuleProps = PropsRuntime<'shell.overlay'> & {
    balance: BalanceRemoteApi;
};
export declare function BalanceCapsule({ balance }: BalanceCapsuleProps): import("react").JSX.Element;
//# sourceMappingURL=BalanceCapsule.d.ts.map