// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {OwnableUpgradeable} from "@openzeppelin/contracts-upgradeable/access/OwnableUpgradeable.sol";

/// @notice Hook interface for ERC-8183 lifecycle callbacks
interface IERC8183Hook {
    function beforeAction(uint256 jobId, bytes4 selector, bytes calldata data) external;
    function afterAction(uint256 jobId, bytes4 selector, bytes calldata data) external;
}

/**
 * @title CompositeRouterHook
 * @notice Composite hook router that chains up to 10 plugin hooks in
 *         priority order, enabling flexible composition of hook behaviors
 *         for ERC-8183 jobs without deploying a new hook address.
 *
 * PROBLEM
 * -------
 * A single ERC-8183 job often needs multiple orthogonal safety checks:
 * token safety screening before funding, trust-score gating before
 * submission, attestation writing after completion. Wiring each job to
 * a different hook address is cumbersome.
 *
 * SOLUTION
 * --------
 * CompositeRouterHook acts as a single hook address that fans out to an
 * ordered list of plugin hooks. Operators compose behavior by adding,
 * removing, or prioritising plugins at runtime — no job re-deployment.
 *
 * FLOW
 * ----
 *  1. createJob(provider, evaluator, expiredAt, description, hook=this)
 *  2. Any lifecycle call (fund, submit, complete, reject, …)
 *     → beforeAction: iterate enabled plugins in ascending priority;
 *       if any plugin reverts, entire call reverts (hard safety gate)
 *     → afterAction: same order; each call wrapped in try/catch so
 *       failures emit PluginAfterActionFailed but don't block the
 *       job state transition (soft observability)
 *  3. Owner manages plugins via add/remove/enable/disable/setPriority
 *
 * EXAMPLE COMPOSITION
 * -------------------
 * ```
 * Router (this contract, set as job hook)
 *   ├── Priority 0: TokenSafetyHook          (beforeAction: block unsafe tokens)
 *   ├── Priority 5: AttestationHook          (afterAction: write BAS receipt)
 *   └── Priority 10: MutualAttestationHook   (afterAction: record for bilateral reviews)
 * ```
 *
 * TRUST MODEL
 * -----------
 * Only the ERC-8183 contract can invoke beforeAction/afterAction.
 * Only the owner can modify the plugin list. Maximum 10 plugins caps
 * gas consumption at a predictable upper bound.
 */
contract CompositeRouterHook is IERC8183Hook, OwnableUpgradeable {
    /*//////////////////////////////////////////////////////////////
                            TYPES
    //////////////////////////////////////////////////////////////*/

    struct Plugin {
        IERC8183Hook hook;
        bool enabled;
        uint256 priority;
    }

    /*//////////////////////////////////////////////////////////////
                            CONSTANTS
    //////////////////////////////////////////////////////////////*/

    /// @notice Maximum number of plugins (gas safety)
    uint256 public constant MAX_PLUGINS = 10;

    /*//////////////////////////////////////////////////////////////
                            STORAGE
    //////////////////////////////////////////////////////////////*/

    /// @notice ERC-8183 contract — used for access control
    address public jobContract;

    /// @notice Array of registered plugins
    Plugin[] private s_plugins;

    /// @notice Mapping to check if a hook address is already registered
    mapping(address => bool) public s_registered;

    /// @dev Reserved storage gap for future upgrades
    uint256[44] private __gap;

    /*//////////////////////////////////////////////////////////////
                            EVENTS
    //////////////////////////////////////////////////////////////*/

    event PluginAdded(address indexed hook, uint256 priority);
    event PluginRemoved(address indexed hook);
    event PluginEnabled(address indexed hook);
    event PluginDisabled(address indexed hook);
    event PluginPriorityUpdated(address indexed hook, uint256 oldPriority, uint256 newPriority);
    event JobContractUpdated(address indexed oldContract, address indexed newContract);
    event PluginAfterActionFailed(address indexed hook, uint256 indexed jobId, bytes reason);

    /*//////////////////////////////////////////////////////////////
                            ERRORS
    //////////////////////////////////////////////////////////////*/

    error ZeroAddress();
    error OnlyJobContract();
    error MaxPluginsReached();
    error PluginAlreadyRegistered(address hook);
    error PluginNotFound(address hook);

    /*//////////////////////////////////////////////////////////////
                            CONSTRUCTOR
    //////////////////////////////////////////////////////////////*/

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    /*//////////////////////////////////////////////////////////////
                            INITIALIZER
    //////////////////////////////////////////////////////////////*/

    /// @param jobContract_ ERC-8183 contract address
    /// @param owner_ Contract owner address
    function initialize(
        address jobContract_,
        address owner_
    ) external initializer {
        if (jobContract_ == address(0)) revert ZeroAddress();
        __Ownable_init(owner_);
        jobContract = jobContract_;
    }

    /*//////////////////////////////////////////////////////////////
                    HOOK CALLBACKS
    //////////////////////////////////////////////////////////////*/

    /// @notice Called before state transitions. Hard gate — any plugin revert blocks the action.
    function beforeAction(uint256 jobId, bytes4 selector, bytes calldata data) external override {
        if (msg.sender != jobContract) revert OnlyJobContract();

        uint256 len = s_plugins.length;
        if (len == 0) return;

        uint256[] memory sortedIndices = _getSortedIndices();

        for (uint256 i = 0; i < len; i++) {
            Plugin storage plugin = s_plugins[sortedIndices[i]];
            if (plugin.enabled) {
                plugin.hook.beforeAction(jobId, selector, data);
            }
        }
    }

    /// @notice Called after state transitions. Soft gate — failures logged but don't block.
    function afterAction(uint256 jobId, bytes4 selector, bytes calldata data) external override {
        if (msg.sender != jobContract) revert OnlyJobContract();

        uint256 len = s_plugins.length;
        if (len == 0) return;

        uint256[] memory sortedIndices = _getSortedIndices();

        for (uint256 i = 0; i < len; i++) {
            Plugin storage plugin = s_plugins[sortedIndices[i]];
            if (plugin.enabled) {
                try plugin.hook.afterAction(jobId, selector, data) {
                    // success
                } catch (bytes memory reason) {
                    emit PluginAfterActionFailed(address(plugin.hook), jobId, reason);
                }
            }
        }
    }

    /*//////////////////////////////////////////////////////////////
                    ADMIN: Plugin Management
    //////////////////////////////////////////////////////////////*/

    /// @notice Add a new plugin hook
    /// @param hook The hook contract address
    /// @param priority Execution priority (lower = earlier)
    function addPlugin(address hook, uint256 priority) external onlyOwner {
        if (hook == address(0)) revert ZeroAddress();
        if (s_registered[hook]) revert PluginAlreadyRegistered(hook);
        if (s_plugins.length >= MAX_PLUGINS) revert MaxPluginsReached();

        s_plugins.push(Plugin({
            hook: IERC8183Hook(hook),
            enabled: true,
            priority: priority
        }));
        s_registered[hook] = true;

        emit PluginAdded(hook, priority);
    }

    /// @notice Remove a plugin hook
    function removePlugin(address hook) external onlyOwner {
        if (!s_registered[hook]) revert PluginNotFound(hook);

        uint256 len = s_plugins.length;
        for (uint256 i = 0; i < len; i++) {
            if (address(s_plugins[i].hook) == hook) {
                if (i != len - 1) {
                    s_plugins[i] = s_plugins[len - 1];
                }
                s_plugins.pop();
                s_registered[hook] = false;
                emit PluginRemoved(hook);
                return;
            }
        }

        revert PluginNotFound(hook);
    }

    /// @notice Enable a disabled plugin
    function enablePlugin(address hook) external onlyOwner {
        if (!s_registered[hook]) revert PluginNotFound(hook);

        uint256 len = s_plugins.length;
        for (uint256 i = 0; i < len; i++) {
            if (address(s_plugins[i].hook) == hook) {
                s_plugins[i].enabled = true;
                emit PluginEnabled(hook);
                return;
            }
        }
    }

    /// @notice Disable a plugin without removing it
    function disablePlugin(address hook) external onlyOwner {
        if (!s_registered[hook]) revert PluginNotFound(hook);

        uint256 len = s_plugins.length;
        for (uint256 i = 0; i < len; i++) {
            if (address(s_plugins[i].hook) == hook) {
                s_plugins[i].enabled = false;
                emit PluginDisabled(hook);
                return;
            }
        }
    }

    /// @notice Update a plugin's execution priority
    function setPluginPriority(address hook, uint256 newPriority) external onlyOwner {
        if (!s_registered[hook]) revert PluginNotFound(hook);

        uint256 len = s_plugins.length;
        for (uint256 i = 0; i < len; i++) {
            if (address(s_plugins[i].hook) == hook) {
                uint256 oldPriority = s_plugins[i].priority;
                s_plugins[i].priority = newPriority;
                emit PluginPriorityUpdated(hook, oldPriority, newPriority);
                return;
            }
        }
    }

    /// @notice Update the ERC-8183 contract reference
    function setJobContract(address jobContract_) external onlyOwner {
        if (jobContract_ == address(0)) revert ZeroAddress();
        address old = jobContract;
        jobContract = jobContract_;
        emit JobContractUpdated(old, jobContract_);
    }

    /*//////////////////////////////////////////////////////////////
                    VIEW
    //////////////////////////////////////////////////////////////*/

    function getPlugins() external view returns (Plugin[] memory) {
        return s_plugins;
    }

    function getPluginCount() external view returns (uint256) {
        return s_plugins.length;
    }

    function isPluginRegistered(address hook) external view returns (bool) {
        return s_registered[hook];
    }

    function getPluginInfo(address hook) external view returns (bool enabled, uint256 priority) {
        if (!s_registered[hook]) revert PluginNotFound(hook);

        uint256 len = s_plugins.length;
        for (uint256 i = 0; i < len; i++) {
            if (address(s_plugins[i].hook) == hook) {
                return (s_plugins[i].enabled, s_plugins[i].priority);
            }
        }

        revert PluginNotFound(hook);
    }

    /*//////////////////////////////////////////////////////////////
                    INTERNAL
    //////////////////////////////////////////////////////////////*/

    /// @dev Insertion sort by priority (ascending). Fine for MAX_PLUGINS=10.
    function _getSortedIndices() internal view returns (uint256[] memory sortedIndices) {
        uint256 len = s_plugins.length;
        sortedIndices = new uint256[](len);

        for (uint256 i = 0; i < len; i++) {
            sortedIndices[i] = i;
        }

        for (uint256 i = 1; i < len; i++) {
            uint256 key = sortedIndices[i];
            uint256 keyPriority = s_plugins[key].priority;
            uint256 j = i;

            while (j > 0 && s_plugins[sortedIndices[j - 1]].priority > keyPriority) {
                sortedIndices[j] = sortedIndices[j - 1];
                j--;
            }
            sortedIndices[j] = key;
        }

        return sortedIndices;
    }
}
