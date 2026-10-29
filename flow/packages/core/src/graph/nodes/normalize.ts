import { normalizeRequest } from '../../normalize.js';
import type { GraphDeps } from '../deps.js';
import { need, type GraphStateUpdate, type GraphStateValue } from '../state.js';

/** Fills request defaults (seed, end month) and derives cycles, calendar and location. */
export const normalizeStep =
  (deps: GraphDeps) =>
  async (s: GraphStateValue): Promise<GraphStateUpdate> => ({
    norm: normalizeRequest(need(s.request, 'request'), deps.today),
  });
