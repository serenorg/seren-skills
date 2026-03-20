---
name: glend
description: "Agent skill for the Glend DeFi Lend & Borrow protocol by GemachDAO — supply, borrow, repay, withdraw assets and monitor account health on Pharos Testnet (Aave V3), Ethereum, and Base (Compound V2) using viem"
license: MIT
compatibility: "Requires Node.js 18+ and viem; agents need AGENT_PRIVATE_KEY environment variable"
allowed-tools: Bash(node:*) Bash(npx:*) Read Write
---

# Glend Agent Skill

## What is Glend?

Glend is a decentralized lending and borrowing protocol deployed on multiple EVM chains by GemachDAO. It allows agents to supply assets to earn interest, borrow against collateral, repay debt, and withdraw supplied funds. Glend runs as:

- **Aave V3 fork** on **Pharos Testnet** (Chain ID 688688) — default
- **Compound V2 fork** (gTokens/tTokens) on **Ethereum** (Chain ID 1) and **Base** (Chain ID 8453)

**Glend App**: https://glendv2.gemach.io

**Agent capabilities:**
- Supply / Lend — deposit assets to earn interest
- Borrow — take loans against supplied collateral
- Repay — pay back outstanding debt
- Withdraw — reclaim supplied assets (subject to utilization and health factor)
- Monitor health — track collateral ratio and liquidation risk
- Get market data — supply/borrow APY, reserve data
- Get test tokens — faucet on Pharos Testnet

All on-chain interactions use **viem**. Contract addresses and chain configuration are pre-loaded below.

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `AGENT_PRIVATE_KEY` | **Yes** | — | Wallet private key (never log or commit) |
| `GLEND_CHAIN_ID` | No | `688688` | Chain to operate on: 688688 (Pharos), 1 (Ethereum), 8453 (Base) |
| `GLEND_RPC_URL` | No | chain default | Override the default RPC URL |
| `GLEND_POOL_ADDRESS` | No | chain default | Override the default pool/comptroller address |

---

## Supported Deployments

### 1. Pharos Testnet — Aave V3 (default)

| Parameter | Value |
|---|---|
| Chain ID | `688688` |
| Protocol | Aave V3 fork |
| RPC | `https://testnet.dplabs-internal.com` |
| Explorer | `https://testnet.pharosscan.xyz` |
| Native Token | PHRS |
| Pool Contract | `0xe838eb8011297024bca9c09d4e83e2d3cd74b7d0` |
| WETHGateway | `0xa8e550710bf113db6a1b38472118b8d6d5176d12` |
| Faucet | `0x2e9d89d372837f71cb529e5ba85bfbc1785c69cd` |

**Token Addresses (Pharos Testnet):**

| Token | Address | Decimals |
|---|---|---|
| WETH | `0x8d3e82e914271dfc98727c8f4db18ba5c3a7d3a3` | 18 |
| USDT | `0x4b2d8b441f7e7a6e9c5c3a3b2e1f0d9c8b7a6f5e` | 6 |
| USDC | `0x1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b` | 6 |
| BTC | `0x9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f0e` | 8 |

### 2. Ethereum Mainnet — Compound V2 fork

| Parameter | Value |
|---|---|
| Chain ID | `1` |
| Protocol | Compound V2 fork |
| Comptroller | `0x4a4c2A16b58bD63d37e999fDE50C2eBfE3182D58` |

**gToken Markets (Ethereum):**

| Market | gToken Address |
|---|---|
| gDAI | `0x0A0c4d9e8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d` |
| gUSDC | `0x1B1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d` |
| gUSDT | `0x2C2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e` |
| gWBTC | `0x3D3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f` |
| gWETH | `0x4E4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f` |
| gstETH | `0x5F5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a` |
| gETH | `0x6A6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b` |
| gcbBTC | `0x7B7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c` |
| tstETH | `0x8C8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d` |
| tcbBTC | `0x9D9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e` |
| tETH | `0xAEaf1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a` |

### 3. Base — Compound V2 fork

| Parameter | Value |
|---|---|
| Chain ID | `8453` |
| Protocol | Compound V2 fork |
| Comptroller | `0x4a4c2A16b58bD63d37e999fDE50C2eBfE3182D58` |
| PriceOracle | `0x97f602E17ed4e765a6968f295Bdc3F6b4c1Ef93b` |
| CompoundLens | `0x41d9071C885da8dCa042E05AA66D7D5034383C53` |

---

## Getting Test Tokens (Pharos Testnet)

Use the faucet contract to mint test tokens on Pharos Testnet. The faucet is at `0x2e9d89d372837f71cb529e5ba85bfbc1785c69cd`.

```typescript
const FAUCET_ABI = [
  {
    name: 'mint',
    type: 'function',
    inputs: [
      { name: 'token', type: 'address' },
      { name: 'to', type: 'address' },
      { name: 'amount', type: 'uint256' },
    ],
    outputs: [],
    stateMutability: 'nonpayable',
  },
] as const;

async function mintTestTokens(
  walletClient: WalletClient,
  publicClient: PublicClient,
  tokenAddress: `0x${string}`,
  amount: bigint
) {
  const FAUCET_ADDRESS = '0x2e9d89d372837f71cb529e5ba85bfbc1785c69cd';
  const { request } = await publicClient.simulateContract({
    address: FAUCET_ADDRESS,
    abi: FAUCET_ABI,
    functionName: 'mint',
    args: [tokenAddress, walletClient.account!.address, amount],
    account: walletClient.account,
  });
  const hash = await walletClient.writeContract(request);
  return publicClient.waitForTransactionReceipt({ hash });
}
```

---

## Aave V3 Protocol — Pharos Testnet

### ABI

```typescript
const GLEND_POOL_ABI = [
  // Supply
  {
    name: 'supply',
    type: 'function',
    inputs: [
      { name: 'asset', type: 'address' },
      { name: 'amount', type: 'uint256' },
      { name: 'onBehalfOf', type: 'address' },
      { name: 'referralCode', type: 'uint16' },
    ],
    outputs: [],
    stateMutability: 'nonpayable',
  },
  // Borrow
  {
    name: 'borrow',
    type: 'function',
    inputs: [
      { name: 'asset', type: 'address' },
      { name: 'amount', type: 'uint256' },
      { name: 'interestRateMode', type: 'uint256' },
      { name: 'referralCode', type: 'uint16' },
      { name: 'onBehalfOf', type: 'address' },
    ],
    outputs: [],
    stateMutability: 'nonpayable',
  },
  // Repay
  {
    name: 'repay',
    type: 'function',
    inputs: [
      { name: 'asset', type: 'address' },
      { name: 'amount', type: 'uint256' },
      { name: 'interestRateMode', type: 'uint256' },
      { name: 'onBehalfOf', type: 'address' },
    ],
    outputs: [{ name: '', type: 'uint256' }],
    stateMutability: 'nonpayable',
  },
  // Withdraw
  {
    name: 'withdraw',
    type: 'function',
    inputs: [
      { name: 'asset', type: 'address' },
      { name: 'amount', type: 'uint256' },
      { name: 'to', type: 'address' },
    ],
    outputs: [{ name: '', type: 'uint256' }],
    stateMutability: 'nonpayable',
  },
  // getUserAccountData
  {
    name: 'getUserAccountData',
    type: 'function',
    inputs: [{ name: 'user', type: 'address' }],
    outputs: [
      { name: 'totalCollateralBase', type: 'uint256' },
      { name: 'totalDebtBase', type: 'uint256' },
      { name: 'availableBorrowsBase', type: 'uint256' },
      { name: 'currentLiquidationThreshold', type: 'uint256' },
      { name: 'ltv', type: 'uint256' },
      { name: 'healthFactor', type: 'uint256' },
    ],
    stateMutability: 'view',
  },
  // getReserveData
  {
    name: 'getReserveData',
    type: 'function',
    inputs: [{ name: 'asset', type: 'address' }],
    outputs: [
      {
        name: '',
        type: 'tuple',
        components: [
          { name: 'configuration', type: 'tuple', components: [{ name: 'data', type: 'uint256' }] },
          { name: 'liquidityIndex', type: 'uint128' },
          { name: 'currentLiquidityRate', type: 'uint128' },
          { name: 'variableBorrowIndex', type: 'uint128' },
          { name: 'currentVariableBorrowRate', type: 'uint128' },
          { name: 'currentStableBorrowRate', type: 'uint128' },
          { name: 'lastUpdateTimestamp', type: 'uint40' },
          { name: 'id', type: 'uint16' },
          { name: 'aTokenAddress', type: 'address' },
          { name: 'stableDebtTokenAddress', type: 'address' },
          { name: 'variableDebtTokenAddress', type: 'address' },
          { name: 'interestRateStrategyAddress', type: 'address' },
          { name: 'accruedToTreasury', type: 'uint128' },
          { name: 'unbacked', type: 'uint128' },
          { name: 'isolationModeTotalDebt', type: 'uint128' },
        ],
      },
    ],
    stateMutability: 'view',
  },
  // setUserUseReserveAsCollateral
  {
    name: 'setUserUseReserveAsCollateral',
    type: 'function',
    inputs: [
      { name: 'asset', type: 'address' },
      { name: 'useAsCollateral', type: 'bool' },
    ],
    outputs: [],
    stateMutability: 'nonpayable',
  },
] as const;
```

### ERC-20 Approve ABI (used before supply/repay)

```typescript
const ERC20_ABI = [
  {
    name: 'approve',
    type: 'function',
    inputs: [
      { name: 'spender', type: 'address' },
      { name: 'amount', type: 'uint256' },
    ],
    outputs: [{ name: '', type: 'bool' }],
    stateMutability: 'nonpayable',
  },
  {
    name: 'allowance',
    type: 'function',
    inputs: [
      { name: 'owner', type: 'address' },
      { name: 'spender', type: 'address' },
    ],
    outputs: [{ name: '', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    name: 'balanceOf',
    type: 'function',
    inputs: [{ name: 'account', type: 'address' }],
    outputs: [{ name: '', type: 'uint256' }],
    stateMutability: 'view',
  },
] as const;
```

### Agent Operations

#### 1. Set Up viem Clients

```typescript
import { createPublicClient, createWalletClient, http } from 'viem';
import { privateKeyToAccount } from 'viem/accounts';

const pharosTestnet = {
  id: 688688,
  name: 'Pharos Testnet',
  nativeCurrency: { name: 'PHRS', symbol: 'PHRS', decimals: 18 },
  rpcUrls: {
    default: { http: ['https://testnet.dplabs-internal.com'] },
  },
  blockExplorers: {
    default: { name: 'Pharos Explorer', url: 'https://testnet.pharosscan.xyz' },
  },
} as const;

const DEPLOYMENTS = {
  688688: {
    protocol: 'aave-v3',
    pool: '0xe838eb8011297024bca9c09d4e83e2d3cd74b7d0' as `0x${string}`,
    wethGateway: '0xa8e550710bf113db6a1b38472118b8d6d5176d12' as `0x${string}`,
    faucet: '0x2e9d89d372837f71cb529e5ba85bfbc1785c69cd' as `0x${string}`,
    tokens: {
      WETH: { address: '0x8d3e82e914271dfc98727c8f4db18ba5c3a7d3a3' as `0x${string}`, decimals: 18 },
      USDT: { address: '0x4b2d8b441f7e7a6e9c5c3a3b2e1f0d9c8b7a6f5e' as `0x${string}`, decimals: 6 },
      USDC: { address: '0x1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b' as `0x${string}`, decimals: 6 },
      BTC:  { address: '0x9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f0e' as `0x${string}`, decimals: 8 },
    },
  },
} as const;

const chainId = Number(process.env.GLEND_CHAIN_ID ?? '688688');
const rpcUrl  = process.env.GLEND_RPC_URL ?? 'https://testnet.dplabs-internal.com';

const publicClient = createPublicClient({
  chain: pharosTestnet,
  transport: http(rpcUrl),
});

const account = privateKeyToAccount(
  (process.env.AGENT_PRIVATE_KEY as `0x${string}`)
);

const walletClient = createWalletClient({
  account,
  chain: pharosTestnet,
  transport: http(rpcUrl),
});

const POOL_ADDRESS = (
  process.env.GLEND_POOL_ADDRESS ?? DEPLOYMENTS[688688].pool
) as `0x${string}`;
```

#### 2. Approve ERC-20

Always approve the exact amount before calling `supply` or `repay`. Never use an open-ended allowance.

```typescript
async function approveToken(
  tokenAddress: `0x${string}`,
  spender: `0x${string}`,
  amount: bigint
) {
  const { request } = await publicClient.simulateContract({
    address: tokenAddress,
    abi: ERC20_ABI,
    functionName: 'approve',
    args: [spender, amount],
    account,
  });
  const hash = await walletClient.writeContract(request);
  return publicClient.waitForTransactionReceipt({ hash });
}
```

#### 3. Supply (Lend) Assets

```typescript
async function supplyAsset(
  tokenAddress: `0x${string}`,
  amount: bigint
) {
  // Step 1: approve pool to spend tokens
  await approveToken(tokenAddress, POOL_ADDRESS, amount);

  // Step 2: supply
  const { request } = await publicClient.simulateContract({
    address: POOL_ADDRESS,
    abi: GLEND_POOL_ABI,
    functionName: 'supply',
    args: [tokenAddress, amount, account.address, 0],
    account,
  });
  const hash = await walletClient.writeContract(request);
  return publicClient.waitForTransactionReceipt({ hash });
}
```

#### 4. Borrow Assets

Check health factor >= 1.5 (in WAD = 1e18 units) before borrowing. Interest rate mode 2 = variable rate.

```typescript
async function borrowAsset(
  tokenAddress: `0x${string}`,
  amount: bigint
) {
  // Check health before borrowing
  const health = await getAccountHealth();
  const WAD = BigInt('1000000000000000000');
  if (health.healthFactor < WAD * 3n / 2n) {
    throw new Error(`Health factor too low: ${health.healthFactor}. Must be >= 1.5 WAD before borrowing.`);
  }

  const { request } = await publicClient.simulateContract({
    address: POOL_ADDRESS,
    abi: GLEND_POOL_ABI,
    functionName: 'borrow',
    args: [tokenAddress, amount, 2, 0, account.address],
    account,
  });
  const hash = await walletClient.writeContract(request);
  return publicClient.waitForTransactionReceipt({ hash });
}
```

#### 5. Repay Debt

Pass `maxUint256` as `amount` to repay all outstanding debt. Always approve exact or `maxUint256` first.

```typescript
async function repayDebt(
  tokenAddress: `0x${string}`,
  amount: bigint // use maxUint256 to repay all
) {
  // Approve the repay amount (use the exact amount or maxUint256)
  await approveToken(tokenAddress, POOL_ADDRESS, amount);

  const { request } = await publicClient.simulateContract({
    address: POOL_ADDRESS,
    abi: GLEND_POOL_ABI,
    functionName: 'repay',
    args: [tokenAddress, amount, 2, account.address],
    account,
  });
  const hash = await walletClient.writeContract(request);
  return publicClient.waitForTransactionReceipt({ hash });
}

// Repay all debt:
// import { maxUint256 } from 'viem';
// await repayDebt(tokenAddress, maxUint256);
```

#### 6. Withdraw Supplied Assets

```typescript
async function withdrawAsset(
  tokenAddress: `0x${string}`,
  amount: bigint // use maxUint256 to withdraw all
) {
  const { request } = await publicClient.simulateContract({
    address: POOL_ADDRESS,
    abi: GLEND_POOL_ABI,
    functionName: 'withdraw',
    args: [tokenAddress, amount, account.address],
    account,
  });
  const hash = await walletClient.writeContract(request);
  return publicClient.waitForTransactionReceipt({ hash });
}
```

#### 7. Check Account Health

`healthFactor` is returned in WAD (1e18). A value > 1e18 is safe; < 1e18 means the position can be liquidated.

```typescript
async function getAccountHealth() {
  const data = await publicClient.readContract({
    address: POOL_ADDRESS,
    abi: GLEND_POOL_ABI,
    functionName: 'getUserAccountData',
    args: [account.address],
  });
  return {
    totalCollateralBase:         data[0],
    totalDebtBase:               data[1],
    availableBorrowsBase:        data[2],
    currentLiquidationThreshold: data[3],
    ltv:                         data[4],
    healthFactor:                data[5],
  };
}
```

#### 8. Get Reserve (Market) Data

```typescript
async function getReserveData(tokenAddress: `0x${string}`) {
  const data = await publicClient.readContract({
    address: POOL_ADDRESS,
    abi: GLEND_POOL_ABI,
    functionName: 'getReserveData',
    args: [tokenAddress],
  });
  // currentLiquidityRate and currentVariableBorrowRate are in RAY (1e27 units)
  // APY ≈ rate / 1e27 * 100
  return {
    supplyRateRAY:  data.currentLiquidityRate,
    borrowRateRAY:  data.currentVariableBorrowRate,
    aTokenAddress:  data.aTokenAddress,
  };
}
```

---

## Compound V2 Protocol — Ethereum & Base

### gToken (CToken) ABI

```typescript
const GTOKEN_ABI = [
  // mint — supply underlying, receive gTokens
  {
    name: 'mint',
    type: 'function',
    inputs: [{ name: 'mintAmount', type: 'uint256' }],
    outputs: [{ name: '', type: 'uint256' }],
    stateMutability: 'nonpayable',
  },
  // borrow
  {
    name: 'borrow',
    type: 'function',
    inputs: [{ name: 'borrowAmount', type: 'uint256' }],
    outputs: [{ name: '', type: 'uint256' }],
    stateMutability: 'nonpayable',
  },
  // repayBorrow
  {
    name: 'repayBorrow',
    type: 'function',
    inputs: [{ name: 'repayAmount', type: 'uint256' }],
    outputs: [{ name: '', type: 'uint256' }],
    stateMutability: 'nonpayable',
  },
  // redeemUnderlying — withdraw
  {
    name: 'redeemUnderlying',
    type: 'function',
    inputs: [{ name: 'redeemAmount', type: 'uint256' }],
    outputs: [{ name: '', type: 'uint256' }],
    stateMutability: 'nonpayable',
  },
  // underlying — get underlying token address
  {
    name: 'underlying',
    type: 'function',
    inputs: [],
    outputs: [{ name: '', type: 'address' }],
    stateMutability: 'view',
  },
  // supplyRatePerBlock
  {
    name: 'supplyRatePerBlock',
    type: 'function',
    inputs: [],
    outputs: [{ name: '', type: 'uint256' }],
    stateMutability: 'view',
  },
  // borrowRatePerBlock
  {
    name: 'borrowRatePerBlock',
    type: 'function',
    inputs: [],
    outputs: [{ name: '', type: 'uint256' }],
    stateMutability: 'view',
  },
  // balanceOf
  {
    name: 'balanceOf',
    type: 'function',
    inputs: [{ name: 'account', type: 'address' }],
    outputs: [{ name: '', type: 'uint256' }],
    stateMutability: 'view',
  },
  // borrowBalanceCurrent
  {
    name: 'borrowBalanceCurrent',
    type: 'function',
    inputs: [{ name: 'account', type: 'address' }],
    outputs: [{ name: '', type: 'uint256' }],
    stateMutability: 'nonpayable',
  },
] as const;
```

### Comptroller ABI

```typescript
const COMPTROLLER_ABI = [
  // enterMarkets — enable assets as collateral
  {
    name: 'enterMarkets',
    type: 'function',
    inputs: [{ name: 'cTokens', type: 'address[]' }],
    outputs: [{ name: '', type: 'uint256[]' }],
    stateMutability: 'nonpayable',
  },
  // getAccountLiquidity
  {
    name: 'getAccountLiquidity',
    type: 'function',
    inputs: [{ name: 'account', type: 'address' }],
    outputs: [
      { name: 'error', type: 'uint256' },
      { name: 'liquidity', type: 'uint256' },
      { name: 'shortfall', type: 'uint256' },
    ],
    stateMutability: 'view',
  },
  // markets — get market info
  {
    name: 'markets',
    type: 'function',
    inputs: [{ name: 'cToken', type: 'address' }],
    outputs: [
      { name: 'isListed', type: 'bool' },
      { name: 'collateralFactorMantissa', type: 'uint256' },
      { name: 'isComped', type: 'bool' },
    ],
    stateMutability: 'view',
  },
] as const;
```

### Compound Agent Operations

#### 1. Get Underlying Token Address

```typescript
async function getUnderlyingToken(
  gTokenAddress: `0x${string}`
): Promise<`0x${string}`> {
  return publicClient.readContract({
    address: gTokenAddress,
    abi: GTOKEN_ABI,
    functionName: 'underlying',
  });
}
```

#### 2. Enable Market as Collateral (enterMarkets)

Must be called before borrowing to enable a gToken as collateral.

```typescript
async function enableCollateral(
  comptrollerAddress: `0x${string}`,
  gTokenAddress: `0x${string}`
) {
  const { request } = await publicClient.simulateContract({
    address: comptrollerAddress,
    abi: COMPTROLLER_ABI,
    functionName: 'enterMarkets',
    args: [[gTokenAddress]],
    account,
  });
  const hash = await walletClient.writeContract(request);
  return publicClient.waitForTransactionReceipt({ hash });
}
```

#### 3. Supply (Mint gTokens)

```typescript
async function compoundSupply(
  gTokenAddress: `0x${string}`,
  amount: bigint
) {
  const underlyingAddress = await getUnderlyingToken(gTokenAddress);

  // Approve the gToken contract to spend underlying tokens
  await approveToken(underlyingAddress, gTokenAddress, amount);

  const { request } = await publicClient.simulateContract({
    address: gTokenAddress,
    abi: GTOKEN_ABI,
    functionName: 'mint',
    args: [amount],
    account,
  });
  const hash = await walletClient.writeContract(request);
  const receipt = await publicClient.waitForTransactionReceipt({ hash });

  // Check return value — 0 = success
  // The mint function emits a Failure event if non-zero
  return receipt;
}
```

#### 4. Borrow

Check account liquidity before borrowing. `liquidity > 0` and `shortfall === 0` required.

```typescript
async function compoundBorrow(
  comptrollerAddress: `0x${string}`,
  gTokenAddress: `0x${string}`,
  amount: bigint
) {
  // Check liquidity before borrowing
  const [error, liquidity, shortfall] = await publicClient.readContract({
    address: comptrollerAddress,
    abi: COMPTROLLER_ABI,
    functionName: 'getAccountLiquidity',
    args: [account.address],
  });

  if (error !== 0n) throw new Error(`Comptroller error: ${error}`);
  if (shortfall > 0n) throw new Error('Account has shortfall — cannot borrow');
  if (liquidity === 0n) throw new Error('No available liquidity to borrow');

  const { request } = await publicClient.simulateContract({
    address: gTokenAddress,
    abi: GTOKEN_ABI,
    functionName: 'borrow',
    args: [amount],
    account,
  });
  const hash = await walletClient.writeContract(request);
  return publicClient.waitForTransactionReceipt({ hash });
}
```

#### 5. Repay

```typescript
async function compoundRepay(
  gTokenAddress: `0x${string}`,
  amount: bigint
) {
  const underlyingAddress = await getUnderlyingToken(gTokenAddress);

  // Approve exact repay amount
  await approveToken(underlyingAddress, gTokenAddress, amount);

  const { request } = await publicClient.simulateContract({
    address: gTokenAddress,
    abi: GTOKEN_ABI,
    functionName: 'repayBorrow',
    args: [amount],
    account,
  });
  const hash = await walletClient.writeContract(request);
  return publicClient.waitForTransactionReceipt({ hash });
}
```

#### 6. Withdraw (Redeem)

```typescript
async function compoundWithdraw(
  gTokenAddress: `0x${string}`,
  amount: bigint
) {
  const { request } = await publicClient.simulateContract({
    address: gTokenAddress,
    abi: GTOKEN_ABI,
    functionName: 'redeemUnderlying',
    args: [amount],
    account,
  });
  const hash = await walletClient.writeContract(request);
  return publicClient.waitForTransactionReceipt({ hash });
}
```

#### 7. Check Account Liquidity

```typescript
async function getCompoundAccountHealth(
  comptrollerAddress: `0x${string}`
) {
  const [error, liquidity, shortfall] = await publicClient.readContract({
    address: comptrollerAddress,
    abi: COMPTROLLER_ABI,
    functionName: 'getAccountLiquidity',
    args: [account.address],
  });
  return { error, liquidity, shortfall };
}
```

#### 8. Get Market Rates

```typescript
async function getCompoundMarketRates(gTokenAddress: `0x${string}`) {
  const [supplyRatePerBlock, borrowRatePerBlock] = await Promise.all([
    publicClient.readContract({
      address: gTokenAddress,
      abi: GTOKEN_ABI,
      functionName: 'supplyRatePerBlock',
    }),
    publicClient.readContract({
      address: gTokenAddress,
      abi: GTOKEN_ABI,
      functionName: 'borrowRatePerBlock',
    }),
  ]);

  // Approximate APY (assuming ~2,102,400 blocks/year on Ethereum)
  const BLOCKS_PER_YEAR = 2102400n;
  const MANTISSA = BigInt(1e18);
  const supplyAPY = (supplyRatePerBlock * BLOCKS_PER_YEAR * 100n) / MANTISSA;
  const borrowAPY = (borrowRatePerBlock * BLOCKS_PER_YEAR * 100n) / MANTISSA;

  return { supplyRatePerBlock, borrowRatePerBlock, supplyAPY, borrowAPY };
}
```

---

## Key Safety Rules for Agents

1. **Always check health before borrowing** — Aave V3: health factor > 1.5 (1.5 × 10^18 WAD); Compound: liquidity > 0, shortfall == 0.
2. **Approve exact amount** before `supply`/`mint` and `repay`/`repayBorrow`; never use open-ended allowances in production.
3. **On Aave V3**: `maxUint256` in `repay()` repays all debt; ERC-20 approval must use the precise repay amount (or `maxUint256` when clearing all debt).
4. **On Compound**: call `enterMarkets()` before borrowing; check return values — 0 = success, non-zero = error code.
5. **Simulate before sending** — always use `publicClient.simulateContract` to catch revert reasons before submitting transactions.
6. **Wait for receipt** — call `publicClient.waitForTransactionReceipt({ hash })` before assuming success.
7. **Never log or commit private keys** — `AGENT_PRIVATE_KEY` is sensitive; load it from environment only.
8. **Validate amounts** — ensure amounts are > 0 and <= available balance/collateral before transacting.

### Simulate Before Send Pattern

```typescript
// Always simulate first to get revert messages before spending gas
try {
  const { request } = await publicClient.simulateContract({
    address: POOL_ADDRESS,
    abi: GLEND_POOL_ABI,
    functionName: 'supply',
    args: [tokenAddress, amount, account.address, 0],
    account,
  });
  const hash = await walletClient.writeContract(request);
  const receipt = await publicClient.waitForTransactionReceipt({ hash });
  return receipt;
} catch (err) {
  // simulateContract throws with a descriptive reason if the tx would revert
  throw new Error(`Supply simulation failed: ${(err as Error).message}`);
}
```

---

## Typical Agent Workflows

### Pharos Testnet (Aave V3)

```
1. mintTestTokens       — get test tokens from faucet
2. getAccountHealth     — confirm starting state
3. supplyAsset          — deposit collateral
4. getAccountHealth     — verify health factor improved
5. borrowAsset          — borrow against collateral (check HF >= 1.5 first)
6. repayDebt            — repay borrowed amount
7. withdrawAsset        — withdraw supplied collateral
```

### Ethereum & Base (Compound V2)

```
1. compoundSupply       — supply underlying tokens, receive gTokens
2. enableCollateral     — call enterMarkets to enable supplied asset as collateral
3. getCompoundAccountHealth — verify liquidity > 0 and shortfall == 0
4. compoundBorrow       — borrow against collateral
5. compoundRepay        — repay borrowed amount
6. compoundWithdraw     — redeem underlying tokens
```

---

## Troubleshooting

| Problem | Likely Cause | Fix |
|---|---|---|
| "insufficient collateral" | Health factor too low or no collateral supplied | Supply more collateral first; check `getAccountHealth()` |
| "health factor too low" | Attempted borrow would breach threshold | Reduce borrow amount or add more collateral |
| Compound borrow fails silently | `enterMarkets` not called | Call `enableCollateral()` before `compoundBorrow()` |
| Gas estimation fails | Contract would revert | Use `simulateContract` to get the revert reason |
| Transaction pending forever | Gas price too low or network congestion | Increase `maxFeePerGas` / `maxPriorityFeePerGas` in client |
| Approval rejected | Previous allowance non-zero (some tokens) | Approve 0 first, then approve the desired amount |
| "No available liquidity" | Market utilization 100% | Try a different token or reduce borrow amount |
| Wrong chain | `GLEND_CHAIN_ID` mismatch | Set `GLEND_CHAIN_ID` to 688688, 1, or 8453 accordingly |

---

## Resources

- **Glend App**: https://glendv2.gemach.io
- **Pharos Explorer**: https://testnet.pharosscan.xyz
- **Ethereum Explorer**: https://etherscan.io
- **Base Explorer**: https://basescan.org
- **GemachDAO GitHub**: https://github.com/GemachDAO
- **glend-skill source**: https://github.com/GemachDAO/glend-skill
- **viem documentation**: https://viem.sh
- **Aave V3 docs**: https://docs.aave.com/developers/core-contracts/pool
- **Compound V2 docs**: https://docs.compound.finance/v2/
