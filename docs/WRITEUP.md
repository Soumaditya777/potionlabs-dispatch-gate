# Write-up — dispatch gate, Ghosh Polymers

**What I decided.** The tool is a one-tap gate pass, not an ERP project. Day mode: the office
sync agent reads open sales invoices from Tally and mirrors them to a tablet in the gate cabin;
the guard picks the invoice, sees invoice number, party, vehicle and e-way bill number already
filled, confirms the plate and prints to a Bluetooth thermal printer. Night mode: because the
office is shut and no invoice exists yet, the same device issues a provisional, auto-numbered
chit pass carrying the plate, party and who phoned the dispatch, which accounts match to the
invoice the next morning. The gate device works from a local database and syncs when it can,
because there is no line to the cabin and the signal dies near the weighbridge. Every pass is
append-only and hash-chained, duplicate references are blocked at issue time, and a daily
reconciliation report lists unmatched chits, reused references and missing e-way bill numbers.
Everything is driven by `config/factory.json` — column mapping, threshold, night window, guard
roster, printer, sync endpoint — so the next plant is a configuration, not a fork.

**What I rejected.** A web app or cloud printer that validates per pass (fails exactly when the
queue is longest); typing the pass from the invoice again (the owner's one hard requirement is
no re-entry); an RFID/fastag boom barrier (spends the whole ₹90,000 on hardware and still needs
the paperwork); biometric driver identity (no master data, no consent process); a full Tally
replacement; and putting the register into a spreadsheet first (the October register shows why
an after-the-fact record is not enough). I also rejected blocking night dispatch until an
invoice exists — that is the business today, and the fix is to make the provisional record
controlled, not to stop trucks.

**Where the language model is used, and why.** One step: reading 50 informal, partly evasive
answers and producing atomic requirements, cross-person conflicts and open questions with a
citation per claim. That is genuinely a language task — the answers are prose, several are
deflections, and the attribution matters. It is not used for the register analysis, the plan,
the budget or the schema: those are deterministic, so the numbers in `register_findings` can be
recomputed from the rows. A verifier drops any claim whose citation is missing, whose persona
does not match the cited speaker, or whose conflict lacks evidence from both sides; dropped
claims are logged in `output/provenance.json`. The shipped run made a real call
(gpt-oss-20b via a public OpenAI-compatible endpoint); a matching response is cached so re-runs
are reproducible.

**Where it breaks.** The register has no consignment value and no e-way bill column, so I can
prove the compliance gap exists but cannot size it — someone has to release invoice values.
Nothing here reads Tally yet: the sync agent is specified, not built, because no Tally export
was available. A night chit is only as good as the phone call behind it; the pass records who
called, but it cannot verify that person's authority. Vehicle master data is absent — 129
entries, 129 different plates — so an allow-list or repeat-carrier check is not possible until
plates are reconciled. I did not build driver identity, weighbridge integration, or a Tally
write-back of vouchers; I also left out multi-user roles and a Hindi/Bengali UI, which the
guard would need in practice. Finally, the ₹90,000 bill of materials is an estimate; it fits
with ₹5,000 contingency, but printer and tablet prices in Kolkata need quoting before purchase.
