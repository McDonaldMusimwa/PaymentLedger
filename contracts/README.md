# contracts

Solidity + Foundry, deployed to Arc testnet. Starting point: fork `circlefin/arc-escrow`.

`PayablesPolicy` — the limits the agent cannot talk its way past: vendor allowlist, per-vendor and daily
caps, human co-sign above a threshold, one settlement per invoiceId, and an `InvoiceSettled` event carrying
the invoice, PO and goods-receipt hashes as proof of payment.

Install Foundry: `curl -L https://foundry.paradigm.xyz | bash && foundryup`
