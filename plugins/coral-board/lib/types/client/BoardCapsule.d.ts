/**
 * BoardCapsule: a bottom-right capsule over shell.overlay showing the coral
 * reasoning-thread task board. Collapsed: one pill with active/candidate
 * counts. Expanded: the active threads (status · title · steps · age · who),
 * done-candidates highlighted. Read-only — archiving stays with the LLM
 * (coral MCP thread_archive), matching the balance capsule's philosophy.
 * Session-free, visible-only 30s poll, zero model tokens.
 */
import type { PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots';
import type { BoardRemoteApi } from './remote.ts';
export type BoardCapsuleProps = PropsRuntime<'shell.overlay'> & {
    board: BoardRemoteApi;
};
export declare function BoardCapsule({ board }: BoardCapsuleProps): import("react").JSX.Element;
//# sourceMappingURL=BoardCapsule.d.ts.map