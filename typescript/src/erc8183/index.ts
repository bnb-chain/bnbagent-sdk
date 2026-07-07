/** ERC-8183 Agentic Commerce Protocol — job types + deliverable/job schemas. */

export {
  JobStatus,
  Verdict,
  REASON_APPROVED,
  REASON_REJECTED,
  ZERO_REASON,
  ZERO_ADDRESS,
  type Job,
} from "./types.js";
export {
  SCHEMA_VERSION,
  DeliverableManifest,
  type DeliverableManifestOpts,
  type DeliverableResponse,
  JobDescription,
  type JobDescriptionOpts,
} from "./schema.js";
