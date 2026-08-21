/**
 * Coral board payloads shared by the node half (service) and the browser
 * half (capsule UI). Pure data — the node half reads coral_threads.json
 * (the reasoning-thread store) and projects each thread into this shape;
 * the browser bundle inlines the type contract only.
 * @module @deepseek-ai/dsh-client-coral-board/types
 */
/** One step of a reasoning thread. */
export interface BoardStep {
    seq: number;
    text: string;
    done?: boolean;
    by?: string;
    at?: number;
}
/** Projected thread row shown in the task board capsule. */
export interface BoardThread {
    /** thread_id (uuid). */
    id: string;
    title: string;
    summary: string;
    /** active | archived | interrupted. */
    status: string;
    /** Total step count. */
    steps: number;
    /** Steps flagged done. */
    doneSteps: number;
    lastAdvanceBy: string;
    /** Epoch millis of last update. */
    updatedAt: number;
    /** Age in days (fraction allowed). */
    ageDays: number;
    /** True when active and looks completed (all steps done, or the latest step
     *  reads as completion — 完成/交付/收官/收工… without 待/未/计划 negation). */
    isCandidate: boolean;
}
//# sourceMappingURL=types.d.ts.map