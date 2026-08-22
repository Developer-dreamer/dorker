"""ATS scrapers — one class per platform.

Each scraper is a thin, dependency-light fetch+parse layer that returns
`Job` instances. Discovery, enrichment, deduplication, and publishing are
kept outside the public scraper API so each scraper stays usable on its own.

>>> from ats_scrapers.scrapers import GreenhouseScraper
>>> jobs = GreenhouseScraper("anthropic").fetch()
"""

from src.scraping.scrapers.adp import ADPWorkforceNowScraper
from src.scraping.scrapers.amazon import AmazonScraper
from src.scraping.scrapers.apple import AppleScraper
from src.scraping.scrapers.arbetsformedlingen import ArbetsformedlingenScraper
from src.scraping.scrapers.ashby import AshbyScraper
from src.scraping.scrapers.avature import AvatureScraper
from src.scraping.scrapers.bamboohr import BambooHRScraper
from src.scraping.scrapers.base import BaseScraper, ScraperRegistry, get_scraper
from src.scraping.scrapers.beisen import BeisenScraper
from src.scraping.scrapers.beisen_legacy import BeisenLegacyScraper
from src.scraping.scrapers.breezy import BreezyScraper
from src.scraping.scrapers.builtin import BuiltInScraper
from src.scraping.scrapers.bundesagentur import BundesagenturScraper
from src.scraping.scrapers.bytedance import BytedanceScraper
from src.scraping.scrapers.cornerstone import CornerstoneScraper
from src.scraping.scrapers.darwinbox import DarwinboxScraper
from src.scraping.scrapers.dayforce import DayforceScraper
from src.scraping.scrapers.eightfold import EightfoldScraper
from src.scraping.scrapers.eures import EuresScraper
from src.scraping.scrapers.gem import GemScraper
from src.scraping.scrapers.getonbrd import GetOnBrdScraper
from src.scraping.scrapers.google import GoogleScraper
from src.scraping.scrapers.greenhouse import GreenhouseScraper
from src.scraping.scrapers.gupy import GupyScraper
from src.scraping.scrapers.herp import HerpScraper
from src.scraping.scrapers.hrmos import HrmosScraper
from src.scraping.scrapers.icims import iCIMSScraper
from src.scraping.scrapers.infojobs_es import InfoJobsSpainScraper
from src.scraping.scrapers.jazzhr import JazzHRScraper
from src.scraping.scrapers.jobbankca import JobBankCAScraper
from src.scraping.scrapers.jobs_cz import JobsCzScraper
from src.scraping.scrapers.jobsch import JobsChScraper
from src.scraping.scrapers.jobvite import JobviteScraper
from src.scraping.scrapers.join_com import JoinComScraper
from src.scraping.scrapers.keka import KekaScraper
from src.scraping.scrapers.lever import LeverScraper
from src.scraping.scrapers.manfred import ManfredScraper
from src.scraping.scrapers.mercor import MercorScraper
from src.scraping.scrapers.meta import MetaScraper
from src.scraping.scrapers.moka import MokaScraper
from src.scraping.scrapers.oracle import OracleScraper
from src.scraping.scrapers.pageup import PageUpScraper
from src.scraping.scrapers.paycom import PaycomScraper
from src.scraping.scrapers.paylocity import PaylocityScraper
from src.scraping.scrapers.personio import PersonioScraper
from src.scraping.scrapers.phenom import PhenomScraper
from src.scraping.scrapers.pinpoint import PinpointScraper
from src.scraping.scrapers.programathor import ProgramathorScraper
from src.scraping.scrapers.recruitee import RecruiteeScraper
from src.scraping.scrapers.recruiterbox import RecruiterboxScraper
from src.scraping.scrapers.remoteok import RemoteOKScraper
from src.scraping.scrapers.rippling import RipplingScraper
from src.scraping.scrapers.seek import SeekScraper
from src.scraping.scrapers.smartrecruiters import SmartRecruitersScraper
from src.scraping.scrapers.softgarden import SoftgardenScraper
from src.scraping.scrapers.successfactors import SuccessFactorsScraper
from src.scraping.scrapers.taleo import TaleoScraper
from src.scraping.scrapers.teamtailor import TeamtailorScraper
from src.scraping.scrapers.tesla import TeslaScraper
from src.scraping.scrapers.thehub import TheHubScraper
from src.scraping.scrapers.tiktok import TikTokScraper
from src.scraping.scrapers.uber import UberScraper
from src.scraping.scrapers.ukg import UKGProScraper
from src.scraping.scrapers.usajobs import USAJobsScraper
from src.scraping.scrapers.wanted import WantedScraper
from src.scraping.scrapers.welcometothejungle import WTTJScraper
from src.scraping.scrapers.wellfound import WellfoundScraper
from src.scraping.scrapers.weworkremotely import WeWorkRemotelyScraper
from src.scraping.scrapers.workable import WorkableScraper
from src.scraping.scrapers.workday import WorkdayScraper
from src.scraping.scrapers.ycombinator import YCombinatorScraper

__all__ = [
    "ADPWorkforceNowScraper",
    "AmazonScraper",
    "AppleScraper",
    "ArbetsformedlingenScraper",
    "AshbyScraper",
    "AvatureScraper",
    "BambooHRScraper",
    "BaseScraper",
    "BeisenLegacyScraper",
    "BeisenScraper",
    "BreezyScraper",
    "BuiltInScraper",
    "BundesagenturScraper",
    "BytedanceScraper",
    "CornerstoneScraper",
    "DarwinboxScraper",
    "DayforceScraper",
    "EightfoldScraper",
    "EuresScraper",
    "GemScraper",
    "GetOnBrdScraper",
    "GoogleScraper",
    "GreenhouseScraper",
    "GupyScraper",
    "HerpScraper",
    "HrmosScraper",
    "InfoJobsSpainScraper",
    "JazzHRScraper",
    "JobBankCAScraper",
    "JobsChScraper",
    "JobsCzScraper",
    "JobviteScraper",
    "JoinComScraper",
    "KekaScraper",
    "LeverScraper",
    "ManfredScraper",
    "MercorScraper",
    "MetaScraper",
    "MokaScraper",
    "OracleScraper",
    "PageUpScraper",
    "PaycomScraper",
    "PaylocityScraper",
    "PersonioScraper",
    "PhenomScraper",
    "PinpointScraper",
    "ProgramathorScraper",
    "RecruiteeScraper",
    "RecruiterboxScraper",
    "RemoteOKScraper",
    "RipplingScraper",
    "ScraperRegistry",
    "SeekScraper",
    "SmartRecruitersScraper",
    "SoftgardenScraper",
    "SuccessFactorsScraper",
    "TaleoScraper",
    "TeamtailorScraper",
    "TeslaScraper",
    "TheHubScraper",
    "TikTokScraper",
    "UKGProScraper",
    "USAJobsScraper",
    "UberScraper",
    "WTTJScraper",
    "WantedScraper",
    "WeWorkRemotelyScraper",
    "WellfoundScraper",
    "WorkableScraper",
    "WorkdayScraper",
    "YCombinatorScraper",
    "get_scraper",
    "iCIMSScraper",
]
