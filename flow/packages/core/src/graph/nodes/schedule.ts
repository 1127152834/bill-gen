import { createRng } from '../../rng.js';
import { ScheduleInfeasibleError, schedule } from '../../schedule/index.js';
import type { Emit } from '../deps.js';
import { canRetry, fallBack, sendBack } from '../retry.js';
import { need, type GraphStateUpdate, type GraphStateValue } from '../state.js';

/**
 * Runs the scheduler with an rng forked per round (`schedule-r<rounds>`), so a send-back draws a
 * new schedule while a resumed run repeats the same one. An infeasible schedule counts as a
 * lifeModel failure: back to lifeModel while rounds remain; otherwise (and for any other error) the
 * job falls back to an earlier result if there is one (`fallBack`), else the error ends it.
 */
export const scheduleStep =
  (emit: Emit) =>
  async (s: GraphStateValue): Promise<GraphStateUpdate> => {
    const norm = need(s.norm, 'norm');
    const model = need(s.model, 'model');
    try {
      const out = schedule(model, norm, createRng(norm.request.seed).fork(`schedule-r${s.rounds}`));
      return { scheduleOut: out, retryTarget: null, feedback: [] };
    } catch (err) {
      if (!(err instanceof ScheduleInfeasibleError) || !canRetry(s)) return fallBack(s, 'schedule', err, emit);
      return {
        scheduleOut: null,
        ...sendBack(s, 'lifeModel', `schedule infeasible: ${err.message}`, [
          `The scheduler could not build the statements: ${err.message}. Use fewer priority 1-2 rows per cycle or a higher opening balance.`,
        ], emit),
      };
    }
  };
