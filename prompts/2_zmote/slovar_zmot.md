# Slovar logičnih zmot

Zaprt slovar imen, iz katerega model v drugem koraku izbere ime zmote. Slovar obsega 32 poimenovanih zmot in vrednost `other` za primer, ko nobeno ime ne ustreza. Kategorijo zmote sistem izpelje iz imena, zato je za vsako ime določena vnaprej.

Vir v kodi: `llm_schemas.py` (`_FORMAL_NAMES`, `_INFORMAL_NAMES`, `_WEAK_NAMES`, `_FALLACY_ALIASES`), slovenska in angleška imena za prikaz: `frontend/src/enumLabels.json`.

## Formalne zmote (`formal`, 6)

Kategorija: napaka v obliki sklepanja.

| Ime v sistemu | Slovensko | Angleško | Sopomenke, ki jih shema preslika na to ime |
|---|---|---|---|
| `illicit_transposition` | nedovoljena obrnitev implikacije | illicit transposition | – |
| `undistributed_middle` | nerazdeljeni srednji člen | undistributed middle | – |
| `affirming_a_disjunct` | potrditev enega člena disjunkcije | affirming a disjunct | – |
| `affirming_the_consequent` | potrditev posledice | affirming the consequent | `affirming_consequent`, `converse_error` |
| `modal_scope_confusion` | zamenjava modalnega obsega | modal scope confusion | `modal_scope` |
| `denying_the_antecedent` | zanikanje predpostavke | denying the antecedent | `denying_antecedent`, `inverse_error` |

## Neformalne zmote (`informal`, 20)

Kategorija: napaka v vsebini ali rabi jezika.

| Ime v sistemu | Slovensko | Angleško | Sopomenke, ki jih shema preslika na to ime |
|---|---|---|---|
| `equivocation` | dvoumna raba izraza | equivocation | `ambiguity` |
| `cherry_picking` | izbiranje ugodnih podatkov | cherry picking | `cherry`, `selective` |
| `no_true_scotsman` | izločitev neprijetnega primera | no true Scotsman | – |
| `circular_reasoning` | krožno sklepanje | circular reasoning | `circular`, `begging_the_question`, `petitio` |
| `ad_hominem` | napad na osebo | ad hominem | `personal_attack` |
| `false_dilemma` | napačna dvojica | false dilemma | `false_dichotomy`, `either_or`, `black_and_white` |
| `false_attribution` | napačno pripisovanje vira | false attribution | `misattribut` |
| `moving_goalposts` | premikanje meril | moving the goalposts | `moving_goalpost` |
| `red_herring` | preusmeritev na nebistveno | red herring | `diversion` |
| `burden_of_proof_shift` | prevalitev dokaznega bremena | shifting the burden of proof | `burden_of_proof`, `shifting_the_burden` |
| `whataboutism` | protiobtoževanje | whataboutism | `tu_quoque` |
| `appeal_to_authority` | sklicevanje na avtoriteto | appeal to authority | `authority` |
| `appeal_to_nature` | sklicevanje na naravnost | appeal to nature | `naturalistic` |
| `appeal_to_ignorance` | sklicevanje na nevednost | appeal to ignorance | `ignorance` |
| `appeal_to_popularity` | sklicevanje na razširjenost | appeal to popularity | `popularity`, `bandwagon`, `ad_populum` |
| `appeal_to_tradition` | sklicevanje na tradicijo | appeal to tradition | `tradition` |
| `appeal_to_emotion` | sklicevanje na čustva | appeal to emotion | `emotional_appeal`, `loaded_language` |
| `straw_man` | slamnati mož | straw man | `straw` |
| `slippery_slope` | spolzka strmina | slippery slope | `slippery` |
| `loaded_question` | vprašanje s podtaknjeno predpostavko | loaded question | `complex_question` |

## Šibko sklepanje (`weak_reasoning`, 6)

Kategorija: sklep je premalo podprt.

| Ime v sistemu | Slovensko | Angleško | Sopomenke, ki jih shema preslika na to ime |
|---|---|---|---|
| `false_equivalence` | napačno enačenje | false equivalence | `false_equivalen`, `false_analogy` |
| `anecdotal_evidence` | posamezen primer kot dokaz | anecdotal evidence | `anecdot` |
| `hasty_generalization` | prenagla posplošitev | hasty generalization | `hasty`, `sweeping_generalization`, `overgeneral` |
| `non_sequitur` | sklep, ki ne sledi | non sequitur | `formal_fallacy` |
| `composition_division` | zamenjava celote in delov | composition or division | `composition`, `division` |
| `post_hoc` | zaporedje kot vzrok | post hoc | `false_cause`, `causal_fallacy` |

## Drugo

| Ime v sistemu | Pomen |
|---|---|
| `other` | nobeno ime iz slovarja ne ustreza; kategorija se ne izpelje |

## Preslikava sopomenk

Ime, ki ga model vrne, sistem zapiše z malimi črkami, vezaje, poševnice in presledke zamenja s podčrtaji. Če ime ni v slovarju, ga primerja s sopomenkami v zgornjih tabelah: ime, ki vsebuje sopomenko, se preslika na pripadajoče ime iz slovarja. Sopomenka `formal_fallacy` se preslika na `non_sequitur`. Če ne ustreza nobena sopomenka, ostane ime tako, kot ga je zapisal model.
