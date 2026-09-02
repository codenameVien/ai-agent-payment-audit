const MAX_RPC_LOG_BLOCKS = 10_000n;

export function recentRpcLogFromBlock(latestBlock: bigint): bigint {
  return latestBlock >= MAX_RPC_LOG_BLOCKS ? latestBlock - MAX_RPC_LOG_BLOCKS + 1n : 0n;
}
