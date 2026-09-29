# ref-papers/ — manifest

Source literature for `paper.pdf` (*Provisional Constants*) and
`grounding-proposal.pdf` (*Grounding the Decision Constants*).

Every file here was **downloaded and its actual text extracted and read**, not
identified from its filename or from URL metadata. That check was necessary: two
of the files initially fetched under a filename were the *wrong paper*, and the
XMP `/Title` metadata in these PDFs is unreliable (`02` reports
"title: R Graphics Output"; `03` reported a title about dispatching rules).
Filenames were corrected after content verification.

**This exercise found five citation errors in the audit documents.** They are
listed in §Errors below and corrected in the `.tex` and `.md` sources.

Only openly-available copies were retrieved. No access control was circumvented.
Four cited works could not be archived; each is marked and the claim relying on
it is flagged in the documents.

## Archived (11 files)

| File | Citation | Version | Verified by |
|---|---|---|---|
| `02-ratios-fieller-guide-arxiv0710.2024.pdf` | Franz, V. H. (2007). *Ratios: A short guide to confidence limits and proper use.* Technical report, JLU Giessen. arXiv:0710.2024. | preprint, 60 pp | title page text |
| `04-morey-et-al-2016-author-preprint.pdf` | Morey, R. D., Hoekstra, R., Rouder, J. N., Lee, M. D., & Wagenmakers, E.-J. *The Fallacy of Placing Confidence in Confidence Intervals.* | **author preprint** (marked "DRAFT"), 43 pp | title page text |
| `05-gelman-carlin2014-type-s-type-m-errors.pdf` | Gelman, A. & Carlin, J. B. (2014). Beyond power calculations: assessing Type S (sign) and Type M (magnitude) errors. *Perspectives on Psychological Science* 9(6):641–651. doi:10.1177/1745691614551642 | version of record (author's copy), 11 pp | header + DOI |
| `06-lakens-et-al-2026-rethinking-type-s-m.pdf` | Lakens, D., Mesquida, C., Xavier-Quintais, G., Rasti, S., Toffalini, E., & Altoè, G. (2026). Rethinking Type S and M errors. OSF preprint 2phzb. | preprint, 27 pp | title page text |
| `07-albers2018-power-from-pilot-data-biased.pdf` | Albers, C. J. & Lakens, D. (2018). When power analyses based on pilot data are biased: inaccurate effect size estimators and follow-up bias. *Journal of Experimental Social Psychology*. | version of record (author's copy), 9 pp | masthead text |
| `08-kass-raftery1995-bayes-factors.pdf` | Kass, R. E. & Raftery, A. E. (1995). Bayes factors. *JASA* 90(430):773–795. | version of record, 24 pp | header |
| `09-gneiting-raftery2007-strictly-proper-scoring.pdf` | Gneiting, T. & Raftery, A. E. (2007). Strictly proper scoring rules, prediction, and estimation. *JASA* 102(477):359–378. doi:10.1198/016214506000001437 | version of record (author's copy), 20 pp | title |
| `11-bakker2020-prereg-power-analyses.pdf` | Bakker, M., Veldkamp, C. L. S., van den Akker, O. R., van Assen, M. A. L. M., Crompvoets, E., Ong, H. H., & Wicherts, J. M. (2020). *PLOS ONE* 15(7):e0236079. doi:10.1371/journal.pone.0236079 | version of record (OA), 15 pp | title + author block |
| `12-lakatos1970-falsification-msrp.pdf` | Lakatos, I. (1970). Falsification and the Methodology of Scientific Research Programs. In *Readings in the Philosophy of Science*, Schick (ed.), Vol. 1. | **reprint in a different reader** (M. Schick, not Lakatos & Musgrave) — pagination differs from the canonical volume, 6 pp | title page text |
| `13-airsbench-arxiv2602.06855.pdf` | Lupidi, A., Gauri, B., Foster, T. S., Al Omari, B., Magka, D., Pepe, A., Audran-Reiss, A., et al. (2026). AIRS-Bench: a Suite of Tasks for Frontier AI Research Science Agents. arXiv:2602.06855. | preprint, 49 pp | title + author list |
| `14-vonluxburg-franz2004-fieller-geometric.pdf` | von Luxburg, U. & Franz, V. H. (2004). Confidence Sets for Ratios: A Purely Geometric Approach To Fieller's Theorem. Max-Planck-Institut für biologische Kybernetik, Technical Report TR-133. | preprint, 11 pp | title page text |

## Cited but NOT archived

| Citation | Why not | How the claim was supported |
|---|---|---|
| Fieller, E. C. (1954). Some problems in interval estimation. *JRSS B* 16:174–185. | 1954; not open access | Volume/page from multiple independent retrieved sources. The mathematical content is also independently reproduced in `02` and `14`, both archived. |
| Gleser, L. J. & Hwang, J. D. (1987). *Biometrika.* | not open access | **Second-hand.** Volume/year from a reference list in a retrieved source; the specific theorem statement is quoted from `axioms2025` (below), not from the original. Flagged as such in both documents. |
| Re-examining confidence intervals for ratios of parameters. *Axioms* 13(3):37, 2025. | **publisher asset-path collision** — the MDPI deploy path returned a *different* article in the same volume (*Heuristic Ensemble Construction Methods…*, Axioms 2024, 13, 37). The downloaded file was deleted rather than left mislabelled. | Full text read on the publisher's HTML page. This is the source of the Gleser–Hwang theorem statement quoted in the documents. |
| Lakens, D., Scheel, A. M., & Isager, P. M. (2018). Equivalence testing for psychological research: a tutorial. *AMPPS* 1(2):259–269. | SAGE paywall | Full text read on the publisher's site, including the "Justifying the Smallest Effect Size of Interest" section. |
| van den Akker, R. et al. (2023). Preregistration in practice. *Behavior Research Methods*. | Springer paywall | Abstract and results read on the publisher's page. |
| National Academies (2019). *Reproducibility and Replicability in Science*, App. D, Table D-1. | free to read at nap.nationalacademies.org; PDF download requires a (free) account | Table D-1 values read directly from the NAP online reader. **This is the source of the 0.3 prior and the 0.624 posterior, and of the 0.795 upper bound that indicts the 0.85 constant.** |

## Errors found by this verification pass

| # | Error | How found | Status |
|---|---|---|---|
| 1 | *Beyond Power Calculations* (Type S/M) attributed to **Lakens**; it is **Gelman & Carlin (2014)** | author copy downloaded from Columbia; header reads "Perspectives on Psychological Science … Gelman, Carlin" | corrected in both `.tex` and `.md` |
| 2 | Albers & Lakens (2018) journal given as *J. Experimental Psychology: General*; it is *J. **Experimental Social** Psychology* | article masthead in the PDF | corrected |
| 3 | The Axioms "ratios" PDF was a **different article entirely** | first page of the PDF | file deleted; citation marked unobtainable |
| 4 | A file named as the 2018 AMPPS SESOI tutorial was byte-identical to the Lakens et al. (2026) preprint | `md5` collision on a duplicated OSF id, confirmed by text | renamed to what it actually is; 2018 tutorial marked unobtainable |
| 5 | Franz's geometric Fieller paper attributed to "Franz 2004"; it is **von Luxburg & Franz (2004)** | title page | corrected |

The common thread is that **URL, filename, and embedded PDF metadata all agreed with each other and were all wrong** in cases 3 and 4. Only reading the text caught them.

## Integrity

```
sha256sum *.pdf   # recorded in the shell history of the build session
```

Re-verify before citing anything here. The two preprints (`04`, `06`) and the
two technical reports (`02`, `14`) are **not** the version of record; where a
page reference matters, check against the published version.
