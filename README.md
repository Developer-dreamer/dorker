# Dorker
> App used to simplify searching and application process

## Stage
**Under an active development**. 

Current tasks:
- Migration from SQLite to PostgreSQL for concurrency writes
- Developing local ML matching pipeline

## Description
This app is an application engine, which is responsible for scraping public ATS systems (forked from: [kalil0321/ats-scrapers](https://github.com/kalil0321/ats-scrapers), so go check it up supported scrapers there) and application matching.

## Run
Run `docker compose -f deployment/docker/docker-compose.yml up --build -d`. Apply migrations within `migrations/postgres` folder and run `fill_ats.py` script, to populate **companies** table. Then run command from root: `python -m orchestration.scraper`. 

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPLv3)** - see the [LICENSE](LICENSE) file for details.

### Commercial Licensing
If you wish to use this software in a proprietary or closed-source environment without adhering to the AGPLv3 requirements, commercial licenses and enterprise support options are available.

Contact us at: `podolancukserafim@gmail.com`
