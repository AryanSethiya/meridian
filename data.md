# Data dictionary

A synthetic snapshot of Meridian Freight's world as of **2026-08-26**: four FreightPro tables,
the dispatch availability sheet, 60 inbound emails and the files attached to them.

```
data/
  emails/                    60 inbound .eml files (RFC 822) to ops@, quotes@, claims@, dispatch@
  attachments/               PDFs and .xls files referenced by those emails
  freightpro/
    loads.csv                800 loads
    carriers.csv             145 carrier records
    shippers.csv             120 shippers
    rates.csv                900 rates
  carrier_availability.csv   90 rows - a dump of the dispatch Google Sheet
```

## `freightpro/loads.csv` — one row per load (a shipment Meridian is brokering)

| column | meaning |
|---|---|
| `LoadID` | Internal integer key |
| `LoadNumber` | Customer-facing load number, `MF-#####`. This is what emails usually quote |
| `Status` | `Available` (no carrier yet), `Covered` (carrier booked), `Dispatched`, `In Transit`, `Delivered`, `Cancelled` — as of the nightly ETL |
| `ShipperID` | → `shippers.csv` |
| `CarrierID` | → `carriers.csv`; blank while the load is `Available` |
| `OriginCity`, `OriginState`, `OriginZip` | Pickup location |
| `DestCity`, `DestState`, `DestZip` | Delivery location |
| `PickupDate`, `DeliveryDate` | Scheduled dates, `YYYY-MM-DD` |
| `EquipmentType` | `Van`, `Reefer` (refrigerated), `Flatbed` |
| `Commodity` | What is being shipped |
| `WeightLbs` | Weight in pounds |
| `Miles` | Distance |
| `CustomerRate` | What Meridian charges the shipper (linehaul, USD) |
| `CarrierRate` | What Meridian pays the carrier (linehaul, USD); blank while `Available` |
| `FuelSurcharge` | Fuel surcharge in USD on top of the linehaul |
| `PONumber` | The shipper's purchase-order number, `PO-#######` |
| `BOLNumber` | Bill-of-lading number, `BOL######` |
| `PRONumber` | The carrier's tracking number, `PRO#######`; often blank |
| `PODReceived` | `Y` if a proof-of-delivery document is on file |
| `ExceptionNotes` | Free text; empty in this export |
| `DriverName`, `DriverPhone` | The assigned driver. **This is PII** (see Legal's memo) |
| `CreatedAt` | When the load was entered |

## `freightpro/carriers.csv` — carrier records as typed by coordinators

| column | meaning |
|---|---|
| `CarrierID` | Internal key |
| `LegalName` | Carrier name **as typed**. The same real carrier appears under several spellings |
| `MCNumber` | Federal motor-carrier number. The same real carrier has the same MC number in every row |
| `DOTNumber` | Federal DOT number |
| `SCAC` | Four-letter carrier code; mostly blank |
| `Phone` | Dispatch phone |
| `Email` | Empty in this export |
| `Status` | `Active`, `Inactive`, `Do Not Use` |

## `freightpro/shippers.csv` — customers

| column | meaning |
|---|---|
| `ShipperID` | Internal key |
| `Name` | Company name |
| `City`, `State`, `Zip` | Head office |
| `ContactEmail` | The shipper's shipping department address |
| `CreditStatus` | `OK` or `Hold`. A shipper on hold should not be quoted or booked without credit sign-off |

## `freightpro/rates.csv` — negotiated and spot rates

| column | meaning |
|---|---|
| `RateID` | Internal key |
| `OriginCity`, `OriginState`, `DestCity`, `DestState` | The lane |
| `EquipmentType` | `Van`, `Reefer`, `Flatbed` |
| `CarrierID` | If set, the rate is with that carrier; if blank, it is a lane-level rate |
| `LinehaulRate` | USD |
| `FuelSurchargePct` | Fuel surcharge as a percentage of the linehaul |
| `EffectiveDate`, `ExpiryDate` | The rate is valid on dates inside this range, inclusive |
| `RateType` | `Contract` (agreed in advance) or `Spot` (one-off) |

Several rates can be valid for the same lane, equipment and date. Choosing between them is a
business rule; there is no column that tells you which one wins.

## `carrier_availability.csv` — the dispatch Google Sheet

| column | meaning |
|---|---|
| `carrier_name` | Free text, typed by dispatch |
| `mc_number` | Matches `carriers.MCNumber` |
| `equipment` | `Van`, `Reefer`, `Flatbed` |
| `available_trucks` | Trucks the carrier said it has free |
| `current_location` | `City, ST` |
| `last_updated` | When dispatch last touched the row. Some rows are weeks old |
| `notes` | e.g. `no NYC`, `needs layover`, `prefers live load` |

## `emails/*.eml`

Standard `.eml` files; Python's `email` module reads them. Headers you will use: `From`, `To`
(which inbox), `Date`, `Subject`. Bodies are plain text. Some emails are forwards, some have
signatures and footers, some contain more than one request. Attachments are MIME parts.

The 60 emails, roughly: 25 quote requests, 9 "where is my truck", 8 claims, 7 load tenders,
5 carrier invoices, 3 that ask two things at once, 3 forwarded or from a third party. Every
category has at least one email that does not go smoothly.

## `attachments/`

- `POD_*.pdf` — proof-of-delivery documents. Some are born-digital (text can be extracted),
  some are photos of paper (no text layer at all).
- `*.xls` — files a shipper called a spreadsheet. Open them before assuming they are Excel.

## How the tables connect

```
shippers ──ShipperID──▶ loads ◀──CarrierID── carriers ◀──MCNumber── carrier_availability
                          │
rates: (OriginCity, DestCity, EquipmentType, date) [+ CarrierID]
emails: MF-##### / PO-####### / BOL###### / PRO####### ──▶ loads
```

Things worth knowing before you design:

- A load number is unique. A PO, BOL or PRO usually maps to one load, but the export does not
  guarantee it.
- One real carrier can have several `carriers.csv` rows. `MCNumber` is the reliable join.
- Status fields are as of the nightly ETL. A load whose delivery date has passed but whose
  status still says `In Transit` is exactly what the reporting database looks like; there is
  no live tracking in FreightPro.
- Email dates run from 2026-08-23 to 2026-08-26. Relative dates in emails ("next Tuesday",
  "tomorrow") are relative to the email's `Date` header.

## Glossary

| term | meaning |
|---|---|
| Shipper | The customer; the company whose goods are being moved |
| Carrier | The trucking company that moves them |
| Broker | Meridian. Sells the move to the shipper, buys it from the carrier |
| Load | One shipment, pickup to delivery |
| Lane | An origin–destination pair, e.g. Chicago → Dallas |
| Tender | A shipper handing Meridian a load to cover |
| Cover | Find and book a carrier for a load |
| Linehaul | The base transport price, before fuel |
| FSC | Fuel surcharge, usually a percentage of the linehaul |
| Quote | Linehaul + FSC offered to a shipper |
| TMS | Transport management system (FreightPro) |
| BOL | Bill of lading: the shipping document that travels with the freight |
| PO | The shipper's purchase-order number for the goods |
| PRO number | The carrier's own tracking number |
| POD | Proof of delivery: the signed document (or photo of it) showing the freight arrived |
| OS&D | Over, short and damaged: the process for freight that arrives wrong |
| Claim | A shipper asking to be compensated for damage, shortage or a refused delivery |
| Consignee / receiver | Who the freight is delivered to |
| Reefer | Refrigerated trailer |
| Van | Dry (unrefrigerated) enclosed trailer |
| Flatbed | Open trailer; steel, lumber, machinery |
| MC number | Federal operating-authority number that identifies a carrier |
| DPA | Data processing agreement: the contract that must exist before personal data goes to a vendor |
