# Low-maintenance investing for a small Canadian account — sourced notes (fetched 2026-10-15)
Not financial or tax advice. This note separates facts read from official pages from general knowledge that was NOT verified here.

## Verified from official pages
| Fact | Source |
|---|---|
| IBKR Canada fixed pricing for Canadian-listed stocks/ETFs: CAD 0.01 per share, minimum CAD 1.00 per order (a tiered plan also exists: CAD 0.008/share, min CAD 1.00). Small third-party clearing fees apply (CAD 0.00017/share, capped CAD 2.00 per order). | https://www.interactivebrokers.com/en/pricing/commissions-stocks.php |
| IBKR GlobalTrader (IMPACT) pricing: Canada CAD 0.01/share min CAD 1.00; US USD 0.005/share min USD 1.00; no account minimums or platform fees. | https://www.interactivebrokers.ca/en/trading/globaltrader/pricing.php |
| IBKR Canada offers a TFSA to Canadian residents only; individual account in the holder's name; no account maintenance fee; no margin; purchases paid in full; withdrawals only in CAD; 2026 annual TFSA limit shown as CAD 7,000; IBKR does not monitor your CRA contribution room and CRA charges 1% per month on over-contributions; no transfers in kind into the TFSA from other accounts at IBKR; a TFSA can hold stocks listed on designated US and Canadian exchanges (excluding Venture-NEX and CSE single-listed). | https://www.interactivebrokers.ca/en/accounts/rsp_tfsa_information.php |
| FHSA is also offered (Canadian residents; CAD 8,000 annual limit, CAD 40,000 lifetime, first-time home buyers). | same page |
| Vanguard All-Equity ETF Portfolio (VEQT): management expense ratio 0.22%; inception Jan 29 2019; trades in CAD; seeks long-term capital growth by investing primarily in equity securities; the asset mix may be reconstituted and rebalanced at the sub-advisor's discretion; Vanguard says it expects to keep absorbing or waiving certain fees but may stop at any time (so the MER can rise). | https://www.vanguard.ca/en/product/etf/asset-allocation/9692/vanguard-all-equity-etf-portfolio |

## Worked example (price ASSUMED, not verified): CAD 600 into one ETF
If the ETF cost about CAD 50 a share, CAD 600 buys 12 shares. Fixed commission = max(12 x 0.01, 1.00) = CAD 1.00, about 0.17% of the purchase, plus a fraction of a cent of clearing fees.
Ongoing cost of an all-in-one fund such as VEQT: 0.22% a year = about CAD 1.32 a year on CAD 600. Check the live price before relying on this arithmetic. Buying the same amount in a few smaller orders costs CAD 1.00 each, so one lump purchase is cheaper at this size.

## What a small account can and cannot do (arithmetic, not a forecast)
At a hypothetical 7% yearly return (an assumption for illustration only; actual returns vary and can be negative), CAD 600 earns about CAD 42 in a year. That is nowhere near CAD 1,000 a month: income of that size has to come from work or a business, not from a small account's returns.

## Trade-offs to understand before choosing
- An all-equity fund holds only stocks, so it can fall sharply in bad years (general knowledge, not verified here: falls of a third or more have happened). Money needed within a few years usually belongs in a savings account or GIC instead.
- A balanced all-in-one ETF holds some bonds and swings less; it also has a different fee and return profile (not researched here).
- TFSA growth and withdrawals are tax-free, but contribution room is personal and tracked by CRA; check it in your CRA My Account before contributing (general knowledge: room starts accruing in the year you turn 18; verify with CRA).
- Fixed commissions make many small purchases expensive relative to a CAD 600 account.
- Trading ETFs inside this project's tools: the IBKR Canada API restriction covers Canadian-exchange products (docs/BROKER_NOTES.md). Manual buying of a Canadian-listed ETF in the IBKR app is a separate matter from automated API orders.

## Not researched / still to verify by the account holder
Current VEQT/XEQT prices and holdings, alternative funds and their fees, the exact tax treatment of foreign withholding tax inside a TFSA, IBKR market-data fees for Canadian exchanges, and whether the unfunded account can be converted to a TFSA at approval.
