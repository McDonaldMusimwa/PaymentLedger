# settlement-service

Node + TypeScript. Owns everything that touches money on Arc:
Circle Wallets (treasury / agent / vendor wallets), Paymaster, App Kit (Send, Swap USDC↔EURC, Bridge/CCTP),
and calls to the `PayablesPolicy` contract. Reports settlement results back to ledger-api.
