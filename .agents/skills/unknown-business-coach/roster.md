# Mentor-roster — UNKNOWN Business Coach

Dit is de **bron van waarheid** voor welke experts de coach kan activeren. Eén regel per mentor.
De orchestrator leest dit bestand aan het begin van een sessie en routeert op basis van de
trigger-signalen. `mentor-maker` voegt hier automatisch een regel toe bij een nieuwe mentor —
zo hoef je de orchestrator-skill zelf nooit te wijzigen.

| Mentor | Domein / fase | Trigger-signalen (waar de mentee mee zit) | Skill om aan te roepen |
|--------|---------------|-------------------------------------------|------------------------|
| **Ideation coach** | Ideation | vaag idee, klant/doelgroep onduidelijk, de *itch* zoeken, merk-/idee-vorming, "wie is mijn klant echt?" | `ideation-coach` |
| **Validation coach** | Validatie & PMF/GTM | "klopt mijn idee?", wat toets ik eerst, koopt iemand dit, riskiest assumption, customer-problem fit, interviews, sales/outbound, positionering, focus | `validation-coach` |
| **Effectuation coach** | Effectuation & strategie | "welke kant op?", strategie onder onzekerheid, "middelen maar geen plan", keuzes maken met wat je hebt, partners/commitments | `effectuation-coach` |
| **Finance coach** | Finance & budgettering | budget maken, runway, cashflow, winst vs cash, kosten/marges, belasting reserveren, eenmanszaak of bv, financiële basics | `finance-coach` |
| **Pricing coach** | Lean pricing / prijsstrategie | welke prijs vraag ik?, hoe prijs ik dit?, pricing, te goedkoop/te duur, prijsstrategie, betalingsbereidheid, verdienmodel | `pricing-coach` |
| **Impact coach** | Impact entrepreneurship | hoe maak ik impact?, purpose/missie, theory of change, impact meten, duurzaam/sociaal ondernemen, stakeholders, people-planet-profit | `impact-coach` |
| **Scaling coach** | Groei & schalen (bestaand bedrijf) | "hoe groei ik?", "hoe schaal ik?", "ik zit vast op X omzet", meer klanten/winst/leads, funnel/kanalen, LTV/CAC, team opschalen — vereist een drááiend bedrijf | `scaling-coach` |

## Disambiguatie (bij overlap)
Eén-regel-beslisregel: gaat de vraag over **het getal/de prijs** → Pricing coach; over
**kosten/runway/cash/belasting** → Finance coach; over **of iemand het überhaupt wíl/koopt** →
Validation coach; over **welke kant op met wat ik heb** → Effectuation coach; over **wat het idee/wie
de klant is** → Ideation coach; over **waarom/purpose/maatschappelijke waarde** → Impact coach; heeft de ondernemer al een **drááiend bedrijf met omzet** en wil hij **groeien/schalen** → Scaling coach (nog geen omzet/product → Validation of Ideation).

## Journey (rode draad die de coach bewaakt)
**Ideation (Ideation coach) → Validatie & riskiest assumption (Validation coach) → Strategie/effectuation (Effectuation coach) →
GTM: interviews, sales, positionering (Validation coach).** Focus & founder-discipline (Validation coach) is een
zijconsult. Forceer de volgorde niet — volg het echte probleem.
