/** ERC-8004 Identity Registry — on-chain agent registration & discovery. */

export {
  ERC8004Agent,
  type CreateErc8004AgentOpts,
  type GenerateAgentUriOpts,
  type AgentRegisterResult,
  type AgentSetUriResult,
  type LocalAgentInfo,
} from "./agent.js";
export { AgentURIGenerator } from "./agentUri.js";
export type { GenerateRegistrationFileOpts } from "./agentUri.js";
export {
  BUILT_WITH_KEY,
  ERC8004_ENV_PREFIX,
  getErc8004Config,
  getBuiltWithValue,
  type Erc8004Config,
} from "./constants.js";
export {
  ContractInterface,
  type AgentInfo,
  type ContractInterfaceOpts,
  type MetadataEntry,
  type RegisterAgentResult,
  type WriteResult,
} from "./contract.js";
export { AgentEndpoint, type AgentEndpointOpts } from "./models.js";
