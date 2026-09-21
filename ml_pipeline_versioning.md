## v0.0.0.

Initial set up. The model expected to process and evaluate job based on the candidate profile and following master prompt

## v0.1.0

The model now expected to extract entities only. Python script evaluates job then deterministically

## v0.1.1

Python evaluation function get updated. Fixed issue with yoe empty in description.

## v0.1.2

Python fixed empty extracted langs and tools scoring

## v0.1.3

Extended JobFactSheet with JobFamily value

## v0.2.0

Migrated pipeline to Guidance framework. Splitted JobFactSheet model to 3 intermediate stages:
LocationEntities, DomainEntities, RedflagsEntities.

## v0.2.1

Switched to thinking model

## v0.2.2

Extended job family enum