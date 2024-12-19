# =============================================================================
# Soluify.com  |  Your #1 IT Problem Solver  |  {SeerrBridge v0.3.4}
# =============================================================================
#  __         _
# (_  _ |   .(_
# __)(_)||_||| \/
#              /
# © 2024
# -----------------------------------------------------------------------------
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from pydantic import BaseModel, Field, ValidationError, field_validator, ConfigDict
from typing import Optional, List, Dict, Any, Union
import asyncio
import json
import time
import os
import sys
import urllib.parse
import re
import inflect
import requests
import platform
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv
from selenium.common.exceptions import StaleElementReferenceException, NoSuchElementException, TimeoutException
from asyncio import Queue
from datetime import datetime, timedelta
from deep_translator import GoogleTranslator
from fuzzywuzzy import fuzz
from loguru import logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from selenium.webdriver.common.keys import Keys
import aiohttp


# Configure loguru
logger.remove()  # Remove default handler
logger.add("seerbridge.log", rotation="500 MB", encoding='utf-8')  # Use utf-8 encoding for log file
logger.add(sys.stdout, colorize=True)  # Ensure stdout can handle Unicode
logger.level("WARNING", color="<cyan>")

# Load environment variables
load_dotenv()

# Initialize FastAPI
app = FastAPI()

# Securely load credentials from environment variables
RD_ACCESS_TOKEN = os.getenv('RD_ACCESS_TOKEN')
RD_REFRESH_TOKEN = os.getenv('RD_REFRESH_TOKEN')
RD_CLIENT_ID = os.getenv('RD_CLIENT_ID')
RD_CLIENT_SECRET = os.getenv('RD_CLIENT_SECRET')
OVERSEERR_BASE = os.getenv('OVERSEERR_BASE')
OVERSEERR_API_BASE_URL = f"{OVERSEERR_BASE}/api/v1"
OVERSEERR_API_KEY = os.getenv('OVERSEERR_API_KEY')
TMDB_API_KEY = os.getenv('TMDB_API_KEY')
HEADLESS_MODE = os.getenv("HEADLESS_MODE", "true").lower() == "true"
ENABLE_AUTOMATIC_BACKGROUND_TASK = os.getenv("ENABLE_AUTOMATIC_BACKGROUND_TASK", "false").lower() == "true"
TORRENT_FILTER_REGEX = os.getenv("TORRENT_FILTER_REGEX")

# Confirm the interval is a valid number.
try:
    REFRESH_INTERVAL_MINUTES = float(os.getenv("REFRESH_INTERVAL_MINUTES"))
except (TypeError, ValueError):
    logger.error("REFRESH_INTERVAL_MINUTES environment variable is not a valid number.")
    exit(1)

if not OVERSEERR_API_BASE_URL:
    logger.error("OVERSEERR_API_BASE_URL environment variable is not set.")
    exit(1)

if not OVERSEERR_API_KEY:
    logger.error("OVERSEERR_API_KEY environment variable is not set.")
    exit(1)

if not TMDB_API_KEY:
    logger.error("TMDB_API_KEY environment variable is not set.")
    exit(1)

# Global driver variable to hold the Selenium WebDriver
driver = None

# Initialize a global queue with a maximum size of 500
request_queue = Queue(maxsize=500)
processing_task = None  # To track the current processing task

class MediaInfo(BaseModel):
    # 1. First: model configuration
    model_config = {
        "populate_by_name": True,
        "extra": "allow",
        "validate_assignment": True,
        "str_strip_whitespace": True,
        "validate_default": True,
        "from_attributes": True
    }

    # 2. Second: field definitions
    media_type: str
    tmdbId: int
    media_id: Optional[int] = None
    id: Optional[int] = None
    status: Optional[Union[int, str]] = None
    status4k: Optional[Union[int, str]] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None

    # 3. Third: validators
    @field_validator('*')
    @classmethod
    def empty_str_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator('status', 'status4k')
    @classmethod
    def validate_status(cls, v):
        if v is None:
            return v
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            try:
                return int(v)
            except ValueError:
                return v
        return v

class SeasonInfo(BaseModel):
    seasonNumber: int
    episodeCount: int
    episodes: List[int]  # List of episode numbers to process

class TVShowRequest(BaseModel):
    title: str
    year: Optional[int]
    seasons: List[SeasonInfo]

class RequestInfo(BaseModel):
    request_id: str
    requestedBy_email: str
    requestedBy_username: str
    requestedBy_avatar: str
    requestedBy_settings_discordId: Optional[str] = None  # Make optional
    requestedBy_settings_telegramChatId: Optional[str] = None  # Make optional

class IssueInfo(BaseModel):
    issue_id: str
    issue_type: str
    issue_status: str
    reportedBy_email: str
    reportedBy_username: str
    reportedBy_avatar: str
    reportedBy_settings_discordId: str
    reportedBy_settings_telegramChatId: str

class CommentInfo(BaseModel):
    comment_message: str
    commentedBy_email: str
    commentedBy_username: str
    commentedBy_avatar: str
    commentedBy_settings_discordId: str
    commentedBy_settings_telegramChatId: str

class WebhookPayload(BaseModel):
    model_config = {
        "populate_by_name": True,
        "extra": "allow",
        "from_attributes": True
    }

    media: MediaInfo
    request: RequestInfo
    issue: Optional[IssueInfo] = None  # Allow issue to be None
    comment: Optional[CommentInfo] = None  # Allow comment to be None
    extra: List[Dict[str, Any]] = []

def refresh_access_token():
    global RD_REFRESH_TOKEN, RD_ACCESS_TOKEN, driver

    TOKEN_URL = "https://api.real-debrid.com/oauth/v2/token"
    data = {
        'client_id': RD_CLIENT_ID,
        'client_secret': RD_CLIENT_SECRET,
        'code': RD_REFRESH_TOKEN,
        'grant_type': 'http://oauth.net/grant_type/device/1.0'
    }
    try:
        logger.info("Requesting a new access token with the refresh token.")
        response = requests.post(TOKEN_URL, data=data)
        response.encoding = 'utf-8'  # Explicitly set UTF-8 encoding for the response
        response_data = response.json()

        if response.status_code == 200:
            expiry_time = int((datetime.now() + timedelta(hours=24)).timestamp() * 1000)
            RD_ACCESS_TOKEN = json.dumps({
                "value": response_data['access_token'],
                "expiry": expiry_time
            }, ensure_ascii=False)  # Ensure non-ASCII characters are preserved
            logger.success("Successfully refreshed access token.")
            
            update_env_file()

            if driver:
                driver.execute_script(f"""
                    localStorage.setItem('rd:accessToken', '{RD_ACCESS_TOKEN}');
                """)
                logger.info("Updated Real-Debrid credentials in local storage after token refresh.")
                driver.refresh()
                logger.success("Refreshed the page after updating local storage with the new token.")
        else:
            logger.error(f"Failed to refresh access token: {response_data.get('error_description', 'Unknown error')}")
    except Exception as e:
        logger.error(f"Error refreshing access token: {e}")

def update_env_file():
    """Update the .env file with the new access token."""
    with open('.env', 'r', encoding='utf-8') as file:
        lines = file.readlines()

    with open('.env', 'w', encoding='utf-8') as file:
        for line in lines:
            if line.startswith('RD_ACCESS_TOKEN'):
                file.write(f'RD_ACCESS_TOKEN={RD_ACCESS_TOKEN}\n')
            else:
                file.write(line)


def check_and_refresh_access_token():
    """Check if the access token is expired or about to expire and refresh it if necessary."""
    global RD_ACCESS_TOKEN
    RD_ACCESS_TOKEN = None  # Reset before reloading
    load_dotenv(override=True)
    RD_ACCESS_TOKEN = os.getenv('RD_ACCESS_TOKEN')
    if RD_ACCESS_TOKEN:
        token_data = json.loads(RD_ACCESS_TOKEN)
        expiry_time = token_data['expiry']  # This is in milliseconds
        current_time = int(time.time() * 1000)  # Convert current time to milliseconds

        # Convert expiry time to a readable date format
        expiry_date = datetime.fromtimestamp(expiry_time / 1000).strftime('%Y-%m-%d %H:%M:%S')

        # Print the expiry date
        logger.info(f"Access token will expire on: {expiry_date}")

        # Check if the token is about to expire in the next 10 minutes (600000 milliseconds)
        if current_time >= expiry_time - 600000:  # 600000 milliseconds = 10 minutes
            logger.info("Access token is about to expire. Refreshing...")
            refresh_access_token()
        else:
            logger.info("Access token is still valid.")
    else:
        logger.error("Access token is not set. Requesting a new token.")
        refresh_access_token()

### Helper function to handle login
def login(driver):
    logger.info("Initiating login process.")

    try:
        # Check if the "Login with Real Debrid" button exists and is clickable
        login_button = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Login with Real Debrid')]"))
        )
        if login_button:
            login_button.click()
            logger.info("Clicked on 'Login with Real Debrid' button.")
        else:
            logger.info("'Login with Real Debrid' button was not found. Skipping this step.")

    except TimeoutException:
        # Handle case where the button was not found before the timeout
        logger.warning("'Login with Real Debrid' button not found or already bypassed. Proceeding...")
    
    except NoSuchElementException:
        # Handle case where the element is not in the DOM
        logger.warning("'Login with Real Debrid' button not present in the DOM. Proceeding...")

    except Exception as ex:
        # Log any other unexpected exception
        logger.error(f"An unexpected error occurred during login: {ex}")


scheduler = AsyncIOScheduler()

### Browser Initialization and Persistent Session
async def initialize_browser():
    global driver
    if driver is None:
        logger.info("Starting persistent browser session.")

        # Detect the current operating system
        current_os = platform.system().lower()  # Returns 'windows', 'linux', or 'darwin' (macOS)
        logger.info(f"Detected operating system: {current_os}")

        options = Options()

        ### Handle Docker/Linux-specific configurations
        if current_os == "linux" and os.getenv("RUNNING_IN_DOCKER", "false").lower() == "true":
            logger.info("Detected Linux environment inside Docker. Applying Linux-specific configurations.")

            # Explicitly set the Chrome binary location
            options.binary_location = os.getenv("CHROME_BIN", "/usr/bin/google-chrome")

            # Enable headless mode for Linux/Docker environments
            options.add_argument("--headless=new")  # Updated modern headless flag
            options.add_argument("--no-sandbox")  # Required for running as root in Docker
            options.add_argument("--disable-dev-shm-usage")  # Handle shared memory limitations
            options.add_argument("--disable-gpu")  # Disable GPU rendering for headless environments
            options.add_argument("--disable-setuid-sandbox")  # Bypass setuid sandbox

        ### Handle Windows-specific configurations
        elif current_os == "windows":
            logger.info("Detected Windows environment. Applying Windows-specific configurations.")

        if HEADLESS_MODE:
            options.add_argument("--headless=new")  # Modern headless mode for Chrome
        options.add_argument("--disable-gpu")  # Disable GPU for Docker compatibility
        options.add_argument("--no-sandbox")  # Required for running browser as root
        options.add_argument("--disable-dev-shm-usage")  # Disable shared memory usage restrictions
        options.add_argument("--disable-setuid-sandbox")  # Disable sandboxing for root permissions
        options.add_argument("--enable-logging")
        options.add_argument("--window-size=1920,1080")  # Set explicit window size to avoid rendering issues

        # WebDriver options to suppress infobars and disable automation detection
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-infobars")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36")


        # Log initialization method
        logger.info("Using WebDriver Manager for dynamic ChromeDriver downloads.")

        try:
            # Use webdriver-manager to install the appropriate ChromeDriver version
            driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

            # Suppress 'webdriver' detection
            driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": """
                Object.defineProperty(navigator, 'webdriver', {
                  get: () => undefined
                })
                """
            })

            logger.success("Initialized Selenium WebDriver with WebDriver Manager.")
            # Navigate to an initial page to confirm browser works
            driver.get("https://debridmediamanager.com")
            logger.success("Navigated to Debrid Media Manager page.")
        except Exception as e:
            logger.error(f"Failed to initialize Selenium WebDriver: {e}")
            raise e

        # Inject Real-Debrid access token and other credentials into local storage
        driver.execute_script(f"""
            localStorage.setItem('rd:accessToken', '{RD_ACCESS_TOKEN}');
        """)
        logger.info("Set Real-Debrid credentials in local storage.")

        # Refresh the page to apply the local storage values
        driver.refresh()
        login(driver)
        logger.success("Refreshed the page to apply local storage values.")
        # After refreshing, call the login function to click the login button
        # After successful login, click on "⚙️ Settings" to open the settings popup
        try:

            logger.info("Attempting to click the '⚙️ Settings' link.")
            settings_link = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//span[contains(text(),'⚙️ Settings')]"))
            )
            settings_link.click()
            logger.info("Clicked on '⚙️ Settings' link.")

            # Locate the "Default torrents filter" input box and insert the regex
            logger.info("Attempting to insert regex into 'Default torrents filter' box.")
            default_filter_input = WebDriverWait(driver, 10).until(

                EC.presence_of_element_located((By.ID, "dmm-default-torrents-filter"))
            )
            default_filter_input.clear()  # Clear any existing filter

            # Use the regex from .env
            default_filter_input.send_keys(TORRENT_FILTER_REGEX)

            logger.info(f"Inserted regex into 'Default torrents filter' input box: {TORRENT_FILTER_REGEX}")

            settings_link.click()
            logger.success("Closed 'Settings' to save settings.")

        except (TimeoutException, NoSuchElementException) as ex:
            logger.error(f"Error while interacting with the settings: {ex}")
            logger.error(f"Continuing without TORRENT_FILTER_REGEX")


        # Navigate to the library section
        logger.info("Navigating to the library section.")
        driver.get("https://debridmediamanager.com/library")

        # Wait for 2 seconds on the library page before further processing
        try:
            # Ensure the library page has loaded correctly (e.g., wait for a specific element on the library page)
            library_element = WebDriverWait(driver, 2).until(
                EC.presence_of_element_located((By.XPATH, "//div[@id='library-content']"))  # Adjust the XPath as necessary
            )
            logger.success("Library section loaded successfully.")
        except TimeoutException:
            logger.info("Library loading.")

        # Wait for at least 2 seconds on the library page
        logger.info("Waiting for 2 seconds on the library page.")
        time.sleep(2)
        logger.success("Completed waiting on the library page.")

async def shutdown_browser():
    global driver
    if driver:
        driver.quit()
        logger.warning("Selenium WebDriver closed.")
        driver = None

### Function to process requests from the queue
async def process_requests():
    """Process requests from Overseerr"""
    try:
        requests = await get_overseerr_media_requests()
        if not requests:
            return
            
        for request in requests:
            try:
                media = request.get('media', {})
                tvdb_id = media.get('tvdbId')
                tmdb_id = media.get('tmdbId')
                
                # If tvdbId exists, it's a TV show
                if tvdb_id:
                    logger.info(f"Processing TV show request (TVDB ID: {tvdb_id}, TMDB ID: {tmdb_id})")
                    await tv_webhook(request)
                else:
                    logger.info(f"Processing movie request (TMDB ID: {tmdb_id})")
                    await process_movie_request(WebhookPayload(**request))
                    
            except Exception as e:
                logger.error(f"Error processing request: {e}")
                continue
                
    except Exception as e:
        logger.error(f"Error in process_requests: {e}")

### Function to add requests to the queue
async def add_request_to_queue(movie_title):
    if request_queue.full():
        logger.warning(f"Request queue is full. Cannot add movie: {movie_title}")
        return False
    
    await request_queue.put(movie_title)
    logger.info(f"Added movie request to queue: {movie_title}")
    return True

### Helper function to extract year from a string
def extract_year(text, ignore_resolution=False):
    if ignore_resolution:
        # Remove resolution strings like "2160p"
        text = re.sub(r'\b\d{3,4}p\b', '', text)
    
    # Extract the year using a regular expression
    match = re.search(r'\b(19\d{2}|20\d{2})\b', text)
    if match:
        return int(match.group(0))
    return None


# Initialize the inflect engine for number-word conversion
p = inflect.engine()

def translate_title(title, target_lang='en'):
    """
    Detects the language of the input title and translates it to the target language.
    """
    try:
        translator = GoogleTranslator(source='auto', target=target_lang)
        translated_title = translator.translate(title)
        logger.info(f"Translated '{title}' to '{translated_title}'")
        return translated_title
    except Exception as e:
        logger.error(f"Error translating title '{title}': {e}")
        return title  # Return the original title if translation fails


def clean_title(title, target_lang='en'):
    """
    Cleans the movie title by removing commas, hyphens, colons, semicolons, and apostrophes,
    translating it to the target language, and converting to lowercase.
    """
    # Translate the title to the target language
    translated_title = translate_title(title, target_lang)

    # Remove commas, hyphens, colons, semicolons, and apostrophes
    cleaned_title = re.sub(r"[,:;'-]", '', translated_title)
    # Replace multiple spaces with a single dot
    cleaned_title = re.sub(r'\s+', '.', cleaned_title)
    # Convert to lowercase for comparison
    return cleaned_title.lower()

def normalize_title(title, target_lang='en'):
    """
    Normalizes the title by ensuring there are no unnecessary spaces or dots,
    translating it to the target language, and converting to lowercase.
    """
    # Replace ellipsis with three periods
    title = title.replace('…', '...')
    # Replace smart apostrophes with regular apostrophes
    title = title.replace('’', "'")
    # Further normalization can be added here if required
    return title.strip()
    # Translate the title to the target language
    translated_title = translate_title(title, target_lang)

    # Replace multiple spaces with a single space and dots with spaces
    normalized_title = re.sub(r'\s+', ' ', translated_title)
    normalized_title = normalized_title.replace('.', ' ')
    # Convert to lowercase
    return normalized_title.lower()


def replace_numbers_with_words(title):
    """
    Replaces digits with their word equivalents (e.g., "3" to "three").
    """
    return re.sub(r'\b\d+\b', lambda x: p.number_to_words(x.group()), title)

def replace_words_with_numbers(title):
    """
    Replaces number words with their digit equivalents (e.g., "three" to "3").
    """
    words_to_numbers = {
        "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
        "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
        "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
        "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
        "eighteen": "18", "nineteen": "19", "twenty": "20"
        # Add more mappings as needed
    }

    # Replace word numbers with digits
    for word, digit in words_to_numbers.items():
        title = re.sub(rf'\b{word}\b', digit, title, flags=re.IGNORECASE)
    return title


# Function to fetch media requests from Overseerr
async def get_overseerr_media_requests():
    """Get media requests from Overseerr"""
    try:
        url = f"{OVERSEERR_API_BASE_URL}/request?take=100&skip=0&filter=processing"
        headers = {"X-Api-Key": OVERSEERR_API_KEY}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    requests = data.get('results', [])
                    logger.info(f"Fetched {len(requests)} requests from Overseerr")
                    
                    # Filter requests that are in "processing" state
                    processing_requests = []
                    for request in requests:
                        media = request.get('media', {})
                        status = request.get('status')
                        
                        # Check if it's in processing status
                        if status == 1:
                            # Determine media type
                            tvdb_id = media.get('tvdbId')
                            media_type = "tv" if tvdb_id else media.get('mediaType')
                            
                            logger.info(f"Found request - Type: {media_type}, TMDB ID: {media.get('tmdbId')}, TVDB ID: {tvdb_id}")
                            processing_requests.append(request)
                            
                    logger.info(f"Filtered {len(processing_requests)} processing requests")
                    return processing_requests
                else:
                    logger.error(f"Failed to fetch requests from Overseerr. Status code: {response.status}")
                    return None
                    
    except Exception as e:
        logger.error(f"Error fetching Overseerr requests: {e}")
        return None

# Trakt API rate limit: 1000 calls every 5 minutes
TRAKT_RATE_LIMIT = 1000
TRAKT_RATE_LIMIT_PERIOD = 5 * 60  # 5 minutes in seconds

trakt_api_calls = 0
last_reset_time = time.time()

def get_movie_details_from_tmdb(tmdb_id: str) -> Optional[dict]:
    """Fetch movie details directly from TMDB API"""
    url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
    params = {
        "api_key": os.getenv('TMDB_API_KEY')
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return {
                "title": data['title'],
                "year": datetime.strptime(data['release_date'], '%Y-%m-%d').year if data.get('release_date') else None
            }
        else:
            logger.error(f"TMDB API request failed with status code {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Error fetching movie details from TMDB API: {e}")
        return None

async def process_movie_request(payload: WebhookPayload):
    try:
        # Extract TMDB ID and request_id from the payload
        tmdb_id = payload.media.tmdbId
        request_id = payload.request.request_id if hasattr(payload, 'request') else None
            
        logger.info(f"Processing request for TMDB ID {tmdb_id} with request_id {request_id}")
        
        # Get movie details from TMDB using the synchronous function
        movie_details = get_movie_details_from_tmdb(tmdb_id)
        if not movie_details:
            logger.error(f"Failed to fetch movie details for TMDB ID {tmdb_id}")
            return {"status": "error", "message": "Failed to fetch movie details"}

        # Format movie title with year
        movie_title = f"{movie_details['title']} ({movie_details['year']})"
        logger.info(f"Processing movie request: {movie_title}")
        
        try:
            # Add timeout handling for search_on_debrid
            confirmation_flag = await asyncio.wait_for(
                asyncio.to_thread(search_on_debrid, movie_title, driver),
                timeout=60.0  # 60 second timeout
            )
            
            if confirmation_flag and request_id:
                # Get the media_id using the request_id
                media_id = get_media_id_from_request(request_id)
                if media_id:
                    if mark_completed(media_id, tmdb_id):
                        logger.success(f"Successfully marked media {media_id} as completed in overseerr")
                    else:
                        logger.error(f"Failed to mark media {media_id} as completed in overseerr")
                else:
                    logger.error(f"Could not find media_id for request_id {request_id}")
            else:
                logger.warning(f"Request {request_id} was not properly confirmed. Skipping marking as completed.")
                
        except asyncio.TimeoutError:
            logger.error(f"Timeout while processing movie request {movie_title}")
            return {"status": "error", "message": "Request processing timed out"}
        except Exception as ex:
            logger.critical(f"Error processing movie request {movie_title}: {ex}")
                
        return {
            "status": "success", 
            "tmdb_id": tmdb_id,
            "request_id": request_id,
            "title": movie_title,
            "message": "Request processed"
        }
        
    except Exception as e:
        logger.error(f"Error processing movie request: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def get_media_id_from_request(request_id: str) -> Optional[int]:
    """Get the media_id from Overseerr using the request_id"""
    url = f"{OVERSEERR_API_BASE_URL}/request/{request_id}"
    headers = {
        "X-Api-Key": OVERSEERR_API_KEY
    }
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            return data.get('media', {}).get('id')
        else:
            logger.error(f"Failed to get request details: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Error getting request details: {e}")
        return None

### Process the fetched messages (newest to oldest)
async def process_movie_requests():
    requests = get_overseerr_media_requests()
    if not requests:
        logger.info("No requests to process")
        return
    
    for request in requests:
        tmdb_id = request['media']['tmdbId']
        media_id = request['media']['id']
        logger.info(f"Processing request with TMDB ID {tmdb_id} and media ID {media_id}")
        
        movie_details = get_movie_details_from_tmdb(tmdb_id)
        if not movie_details:
            logger.error(f"Failed to get movie details for TMDB ID {tmdb_id}")
            continue
        
        movie_title = f"{movie_details['title']} ({movie_details['year']})"
        logger.info(f"Processing movie request: {movie_title}")
        
        try:
            confirmation_flag = await asyncio.to_thread(search_on_debrid, movie_title, driver)  # Process the request and get the confirmation flag
            if confirmation_flag:
                if mark_completed(media_id, tmdb_id):
                    logger.success(f"Marked media {media_id} as completed in overseerr")
                else:
                    logger.error(f"Failed to mark media {media_id} as completed in overseerr")
            else:
                logger.info(f"Media {media_id} was not properly confirmed. Skipping marking as completed.")
        except Exception as ex:
            logger.critical(f"Error processing movie request {movie_title}: {ex}")

    logger.info("Finished processing all current requests. Waiting for new requests.")

def mark_completed(media_id: int, tmdb_id: int) -> bool:
    """Mark item as completed in overseerr"""
    url = f"{OVERSEERR_API_BASE_URL}/media/{media_id}/available"
    headers = {
        "X-Api-Key": OVERSEERR_API_KEY,
        "Content-Type": "application/json"
    }
    data = {"is4k": False}
    
    try:
        response = requests.post(url, headers=headers, json=data)
        response_data = response.json()  # Parse the JSON response
        
        if response.status_code == 200:
            # Verify that the response contains the correct tmdb_id
            if response_data.get('tmdbId') == tmdb_id:
                logger.info(f"Marked media {media_id} as completed in overseerr. Response: {response_data}")
                return True
            else:
                logger.error(f"TMDB ID mismatch for media {media_id}. Expected {tmdb_id}, got {response_data.get('tmdbId')}")
                return False
        else:
            logger.error(f"Failed to mark media as completed in overseerr with id {media_id}: Status code {response.status_code}, Response: {response_data}")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to mark media as completed in overseerr with id {media_id}: {str(e)}")
        return False
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON response for media {media_id}: {str(e)}")
        return False


def prioritize_buttons_in_box(result_box):
    """
    Prioritize buttons within a result box. Clicks the 'Instant RD' or 'DL with RD' button
    if available. Handles stale element references by retrying the operation once.

    Args:
        result_box (WebElement): The result box element.

    Returns:
        bool: True if a button was successfully clicked and handled, False otherwise.
    """
    try:
        # Attempt to locate the 'Instant RD' button
        instant_rd_button = result_box.find_element(By.XPATH, ".//button[contains(@class, 'bg-green-900/30')]")
        logger.info("Located 'Instant RD' button.")

        # Attempt to click the button and wait for a state change
        if attempt_button_click_with_state_check(instant_rd_button, result_box):
            return True

    except NoSuchElementException:
        logger.info("'Instant RD' button not found. Checking for 'DL with RD' button.")

    except StaleElementReferenceException:
        logger.warning("Stale element reference encountered for 'Instant RD' button. Retrying...")
        # Retry once by re-locating the button
        try:
            instant_rd_button = result_box.find_element(By.XPATH, ".//button[contains(@class, 'bg-green-900/30')]")
            if attempt_button_click_with_state_check(instant_rd_button, result_box):
                return True
        except Exception as e:
            logger.error(f"Retry failed for 'Instant RD' button due to: {e}")

    try:
        # If the 'Instant RD' button is not found, try to locate the 'DL with RD' button
        dl_with_rd_button = result_box.find_element(By.XPATH, ".//button[contains(text(), 'DL with RD')]")
        logger.info("Located 'DL with RD' button.")

        # Attempt to click the button and wait for a state change
        if attempt_button_click_with_state_check(dl_with_rd_button, result_box):
            return True

    except NoSuchElementException:
        logger.warning("Neither 'Instant RD' nor 'DL with RD' button found in this box.")

    except StaleElementReferenceException:
        logger.warning("Stale element reference encountered for 'DL with RD' button. Retrying...")
        # Retry once by re-locating the button
        try:
            dl_with_rd_button = result_box.find_element(By.XPATH, ".//button[contains(text(), 'DL with RD')]")
            if attempt_button_click_with_state_check(dl_with_rd_button, result_box):
                return True
        except Exception as e:
            logger.error(f"Retry failed for 'DL with RD' button due to: {e}")

    except Exception as e:
        logger.error(f"An unexpected error occurred while prioritizing buttons: {e}")

    return False


def attempt_button_click_with_state_check(button, result_box):
    """
    Attempts to click a button and waits for its state to change.

    Args:
        button (WebElement): The button element to click.
        result_box (WebElement): The parent result box (used for context).

    Returns:
        bool: True if the button's state changes, False otherwise.
    """
    try:
        # Get the initial state of the button
        initial_state = button.get_attribute("class")  # Or another attribute relevant to the state
        logger.info(f"Initial button state: {initial_state}")

        # Click the button
        button.click()
        logger.info("Clicked the button.")

        # Wait for a short period (max 2 seconds) to check for changes in the state
        WebDriverWait(result_box, 2).until(
            lambda driver: button.get_attribute("class") != initial_state
        )
        logger.info("Button state changed successfully after clicking.")
        return True  # Button was successfully clicked and handled

    except TimeoutException:
        logger.warning("No state change detected after clicking the button within 2 seconds.")

    except StaleElementReferenceException:
        logger.error("Stale element reference encountered while waiting for button state change.")

    return False




### Search Function to Reuse Browser
def search_on_debrid(movie_title, driver):
    logger.info(f"Starting Selenium automation for movie: {movie_title}")

    # Check if the driver is None before proceeding to avoid NoneType errors
    if not driver:
        logger.error("Selenium WebDriver is not initialized. Attempting to reinitialize.")
        driver = initialize_browser()

    debrid_media_manager_base_url = "https://debridmediamanager.com/search?query="
    
    # Use urllib to encode the movie title safely, handling all special characters including '&', ':', '(', ')'
    encoded_movie_title = urllib.parse.quote(movie_title)
    
    url = debrid_media_manager_base_url + encoded_movie_title
    logger.info(f"Search URL: {url}")

    try:
        # Directly jump to the search results page after login
        driver.get(url)
        logger.success(f"Navigated to search results page for {movie_title}.")
        
        # Wait for the results page to load dynamically
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.XPATH, "//a[contains(@href, '/movie/')]"))
        )

        # Clean and normalize the movie title (remove year in parentheses)
        movie_title_cleaned = movie_title.split('(')[0].strip()
        movie_title_normalized = normalize_title(movie_title_cleaned)
        logger.info(f"Searching for normalized movie title: {movie_title_normalized}")

        # Find the movie result elements
        try:
            movie_elements = WebDriverWait(driver, 10).until(
                EC.presence_of_all_elements_located((By.XPATH, f"//a[contains(@href, '/movie/')]"))
            )

            # Iterate over the movie elements to find the correct one
            for movie_element in movie_elements:
                movie_title_element = movie_element.find_element(By.XPATH, ".//h3")
                movie_year_element = movie_element.find_element(By.XPATH, ".//div[contains(@class, 'text-gray-600')]")
                
                search_title_cleaned = movie_title_element.text.strip()
                search_title_normalized = normalize_title(search_title_cleaned)
                search_year = extract_year(movie_year_element.text)

                # Extract the expected year from the provided movie title (if it exists)
                expected_year = extract_year(movie_title)

                # Use fuzzy matching to allow for minor differences in titles
                title_match_ratio = fuzz.ratio(search_title_normalized.lower(), movie_title_normalized.lower())
                logger.info(f"Comparing '{search_title_normalized}' with '{movie_title_normalized}' (Match Ratio: {title_match_ratio})")

                # Check if the titles match (with a threshold) and if the years are within ±1 year range
                if title_match_ratio >= 69 and (expected_year is None or abs(search_year - expected_year) <= 1):
                    logger.info(f"Found matching movie: {search_title_cleaned} ({search_year})")
                    
                    # Click on the parent <a> tag (which is the clickable link)
                    parent_link = movie_element
                    parent_link.click()
                    logger.success(f"Clicked on the movie link for {search_title_cleaned}")
                    break
            else:
                logger.error(f"No matching movie found for {movie_title_cleaned} ({expected_year})")
                return
        except (TimeoutException, NoSuchElementException) as e:
            logger.critical(f"Failed to find or click on the search result: {movie_title}")
            return

        confirmation_flag = False  # Initialize the confirmation flag

        # Wait for the movie's details page to load by listening for the status message
        try:
            # Step 1: Check for Status Message
            try:
                no_results_element = WebDriverWait(driver, 2).until(
                    EC.text_to_be_present_in_element(
                        (By.XPATH, "//div[@role='status' and contains(@aria-live, 'polite')]"),
                        "No results found"
                    )
                )
                logger.warning("'No results found' message detected. Skipping further checks.")
                return  # Skip further checks if "No results found" is detected
            except TimeoutException:
                logger.warning("'No results found' message not detected. Proceeding to check for available torrents.")

            try:
                status_element = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located(
                        (By.XPATH, "//div[@role='status' and contains(@aria-live, 'polite') and contains(text(), 'available torrents in RD')]")
                    )
                )
                status_text = status_element.text
                logger.info(f"Status message: {status_text}")

                # Extract the number of available torrents from the status message (look for the number)
                torrents_match = re.search(r"Found (\d+) available torrents in RD", status_text)
                if torrents_match:
                    torrents_count = int(torrents_match.group(1))
                    logger.info(f"Found {torrents_count} available torrents in RD.")
                else:
                    logger.warning("Could not find the expected 'Found X available torrents in RD' message. Proceeding to check for 'Checking RD availability...'.")
            except TimeoutException:
                logger.warning("Timeout waiting for the RD status message. Proceeding with the next steps.")
                status_text = None  # No status message found, but continue

            logger.info("Waiting for 'Checking RD availability...' to appear.")
            
            # Step 2: Check if any red buttons (RD 100%) exist and verify the title for each
            try:
                red_buttons_elements = driver.find_elements(By.XPATH, "//button[contains(@class, 'bg-red-900/30')]")
                logger.info(f"Found {len(red_buttons_elements)} red button(s) (100% RD). Verifying titles before deciding to skip.")

                for i, red_button_element in enumerate(red_buttons_elements, start=1):
                    logger.info(f"Checking red button {i}...")

                    try:
                        # Adjusted XPath to find the <h2> element within the same parent container as the red button
                        red_button_title_element = red_button_element.find_element(By.XPATH, ".//ancestor::div[contains(@class, 'border-2')]//h2")
                        red_button_title_text = red_button_title_element.text.strip()

                        # Clean the title for comparison
                        red_button_title_cleaned = clean_title(red_button_title_text.split('(')[0].strip(), target_lang='en')
                        red_button_title_normalized = normalize_title(red_button_title_text.split('(')[0].strip(), target_lang='en')

                        # Clean and normalize the movie title once outside the loop
                        movie_title_cleaned = clean_title(movie_title.split('(')[0].strip(), target_lang='en')
                        movie_title_normalized = normalize_title(movie_title.split('(')[0].strip(), target_lang='en')

                        logger.info(f"Red button {i} title: {red_button_title_cleaned}, Expected movie title: {movie_title_cleaned}")

                        # Extract the year from the red button title, ignoring resolution strings
                        red_button_year = extract_year(red_button_title_text, ignore_resolution=True)

                        # Extract the expected year from the provided movie title (if it exists)
                        expected_year = extract_year(movie_title)

                        # Use fuzzy matching instead of startswith (also allow partial matching for more relaxed comparisons)
                        title_match_ratio = fuzz.partial_ratio(red_button_title_cleaned.lower(), movie_title_cleaned.lower())
                        title_match_threshold = 75  # You can lower or raise this based on how aggressive you want it to be.

                        # Additional title comparison logic with fuzzy matching and threshold
                        title_matched = False
                        if (
                            title_match_ratio >= title_match_threshold or  # Fuzzy match title (relaxed match)
                            movie_title_normalized.startswith(red_button_title_normalized)  # Backwards title check
                        ):
                            # Titles match within boundaries.
                            title_matched = True  # Consider this a valid match.

                        # Expanded year match check with more leeway (±2 years to avoid overly strict checks)
                        year_matched = (expected_year is None or abs(red_button_year - expected_year) <= 2)

                        if title_matched and year_matched:
                            logger.info(f"Found a match on red button {i} - {red_button_title_cleaned}. Skipping...")
                            # Handle the RD button selection or matching action here
                            confirmation_flag = True  # Mark as confirmed match.
                            return confirmation_flag  # Once we click a matching button, we stop further checks.

                        else:
                            # If no match, continue with the next available RD red button.
                            logger.warning(f"No match for red button {i}: Title - {red_button_title_cleaned}, Year - {red_button_year}. Moving to next red button.")

                    except NoSuchElementException as e:
                        logger.warning(f"Could not find title associated with red button {i}: {e}")
                        continue  # If a title is not found for a red button, continue to the next one
            except NoSuchElementException:
                logger.info("No red buttons (100% RD) detected. Proceeding with optional fallback.")


            # Step 3: Wait for the "Checking RD availability..." message to disappear
            try:
                WebDriverWait(driver, 15).until_not(
                    EC.text_to_be_present_in_element(
                        (By.XPATH, "//div[@role='status' and contains(@aria-live, 'polite')]"),
                        "Checking RD availability"
                    )
                )
                logger.info("'Checking RD availability...' has disappeared. Now waiting for RD results.")
            except TimeoutException:
                logger.warning("'Checking RD availability...' did not disappear within 15 seconds. Proceeding to the next steps.")

            # Step 4: Wait for the "Found X available torrents in RD" message
            try:
                status_element = WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located(
                        (By.XPATH, "//div[@role='status' and contains(@aria-live, 'polite') and contains(text(), 'available torrents in RD')]")
                    )
                )
                status_text = status_element.text
                logger.info(f"Status message: {status_text}")
            except TimeoutException:
                logger.warning("Timeout waiting for the RD status message. Proceeding with the next steps.")
                status_text = None  # No status message found, but continue

            # Step 5: Extract the number of available torrents from the status message (look for the number)
            if status_text:
                torrents_match = re.search(r"Found (\d+) available torrents in RD", status_text)

                if torrents_match:
                    torrents_count = int(torrents_match.group(1))
                    logger.success(f"Found {torrents_count} available torrents in RD.")
                else:
                    logger.warning("Could not find the expected 'Found X available torrents in RD' message. Proceeding to check for Instant RD.")
                    torrents_count = 0  # Default to 0 torrents if no match found
            else:
                logger.warning("No status text available. Proceeding to check for Instant RD.")
                torrents_count = 0  # Default to 0 torrents if no status text

            # Step 6: If the status says "0 torrents", check if there's still an Instant RD button
            if torrents_count == 0:
                logger.warning("No torrents found in RD according to status, but checking for Instant RD buttons.")
            else:
                logger.success(f"{torrents_count} torrents found in RD. Proceeding with RD checks.")
            
            # Step 7: Check if any red button (RD 100%) exists again before continuing
            try:
                red_buttons_elements = driver.find_elements(By.XPATH, "//button[contains(@class, 'bg-red-900/30')]")
                logger.info(f"Found {len(red_buttons_elements)} red button(s) (100% RD). Verifying titles before deciding to skip.")

                for i, red_button_element in enumerate(red_buttons_elements, start=1):
                    logger.info(f"Checking red button {i}...")

                    try:
                        # Adjusted XPath to find the <h2> element within the same parent container as the red button
                        red_button_title_element = red_button_element.find_element(By.XPATH, ".//ancestor::div[contains(@class, 'border-2')]//h2")
                        red_button_title_text = red_button_title_element.text.strip()

                        # Clean the title for comparison
                        red_button_title_cleaned = clean_title(red_button_title_text.split('(')[0].strip(), target_lang='en')
                        red_button_title_normalized = normalize_title(red_button_title_text.split('(')[0].strip(), target_lang='en')

                        # Clean and normalize the movie title once outside the loop
                        movie_title_cleaned = clean_title(movie_title.split('(')[0].strip(), target_lang='en')
                        movie_title_normalized = normalize_title(movie_title.split('(')[0].strip(), target_lang='en')

                        logger.info(f"Red button {i} title: {red_button_title_cleaned}, Expected movie title: {movie_title_cleaned}")

                        # Extract the year from the red button title, ignoring resolution strings
                        red_button_year = extract_year(red_button_title_text, ignore_resolution=True)

                        # Extract the expected year from the provided movie title (if it exists)
                        expected_year = extract_year(movie_title)

                        # Use fuzzy matching instead of startswith (also allow partial matching for more relaxed comparisons)
                        title_match_ratio = fuzz.partial_ratio(red_button_title_cleaned.lower(), movie_title_cleaned.lower())
                        title_match_threshold = 75  # You can lower or raise this based on how aggressive you want it to be.

                        # Additional title comparison logic with fuzzy matching and threshold
                        title_matched = False
                        if (
                            title_match_ratio >= title_match_threshold or  # Fuzzy match title (relaxed match)
                            movie_title_normalized.startswith(red_button_title_normalized)  # Backwards title check
                        ):
                            # Titles match within boundaries.
                            title_matched = True  # Consider this a valid match.

                        # Expanded year match check with more leeway (±2 years to avoid overly strict checks)
                        year_matched = (expected_year is None or abs(red_button_year - expected_year) <= 2)

                        if title_matched and year_matched:
                            logger.info(f"Found a match on red button {i} - {red_button_title_cleaned}. Skipping...")
                            # Handle the RD button selection or matching action here
                            confirmation_flag = True  # Mark as confirmed match.
                            return confirmation_flag  # Once we click a matching button, we stop further checks.

                        else:
                            # If no match, continue with the next available RD red button.
                            logger.warning(f"No match for red button {i}: Title - {red_button_title_cleaned}, Year - {red_button_year}. Moving to next red button.")

                    except NoSuchElementException as e:
                        logger.warning(f"Could not find title associated with red button {i}: {e}")
                        continue  # If a title is not found for a red button, continue to the next one
            except NoSuchElementException:
                logger.info("No red buttons (100% RD) detected. Proceeding with optional fallback.")


            # After clicking the matched movie title, we now check the popup boxes for Instant RD buttons
            # Step 8: Check the result boxes with the specified class for "Instant RD"
            try:
                result_boxes = WebDriverWait(driver, 10).until(
                    EC.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'border-black')]"))
                )

                for i, result_box in enumerate(result_boxes, start=1):
                    try:
                        # Extract the title from the result box
                        title_element = result_box.find_element(By.XPATH, ".//h2")
                        title_text = title_element.text.strip()
                        logger.info(f"Box {i} title: {title_text}")

                        # Extract the year from the title
                        box_year = extract_year(title_text)
                        if box_year is None:
                            logger.warning(f"Could not extract year from '{title_text}'. Skipping box {i}.")
                            continue

                        # Clean both the movie title and the box title for comparison
                        movie_title_cleaned = clean_title(movie_title.split('(')[0].strip(), target_lang='en')
                        title_text_cleaned = clean_title(title_text.split(str(box_year))[0].strip(), target_lang='en')

                        movie_title_normalized = normalize_title(movie_title.split('(')[0].strip(), target_lang='en')
                        title_text_normalized = normalize_title(title_text.split(str(box_year))[0].strip(), target_lang='en')


                        # Convert digits to words for comparison
                        movie_title_cleaned_word = replace_numbers_with_words(movie_title_cleaned)
                        title_text_cleaned_word = replace_numbers_with_words(title_text_cleaned)
                        movie_title_normalized_word = replace_numbers_with_words(movie_title_normalized)
                        title_text_normalized_word = replace_numbers_with_words(title_text_normalized)

                        # Convert words to digits for comparison
                        movie_title_cleaned_digit = replace_words_with_numbers(movie_title_cleaned)
                        title_text_cleaned_digit = replace_words_with_numbers(title_text_cleaned)
                        movie_title_normalized_digit = replace_words_with_numbers(movie_title_normalized)
                        title_text_normalized_digit = replace_words_with_numbers(title_text_normalized)

                        # Log all variations for debugging
                        logger.info(f"Cleaned movie title: {movie_title_cleaned}, Cleaned box title: {title_text_cleaned}")
                        logger.info(f"Normalized movie title: {movie_title_normalized}, Normalized box title: {title_text_normalized}")
                        logger.info(f"Movie title (digits to words): {movie_title_cleaned_word}, Box title (digits to words): {title_text_cleaned_word}")
                        logger.info(f"Movie title (words to digits): {movie_title_cleaned_digit}, Box title (words to digits): {title_text_cleaned_digit}")

                        # Compare the title in all variations
                        if not (
                            fuzz.partial_ratio(title_text_cleaned.lower(), movie_title_cleaned.lower()) >= 75 or
                            fuzz.partial_ratio(title_text_normalized.lower(), movie_title_normalized.lower()) >= 75 or
                            fuzz.partial_ratio(title_text_cleaned_word.lower(), movie_title_cleaned_word.lower()) >= 75 or
                            fuzz.partial_ratio(title_text_normalized_word.lower(), movie_title_normalized_word.lower()) >= 75 or
                            fuzz.partial_ratio(title_text_cleaned_digit.lower(), movie_title_cleaned_digit.lower()) >= 75 or
                            fuzz.partial_ratio(title_text_normalized_digit.lower(), movie_title_normalized_digit.lower()) >= 75
                        ):
                            logger.warning(f"Title mismatch for box {i}: {title_text_cleaned} or {title_text_normalized} (Expected: {movie_title_cleaned} or {movie_title_normalized}). Skipping.")
                            continue  # Skip this box if none of the variations match

                        # Compare the year with the expected year (allow 1 year)
                        expected_year = extract_year(movie_title)
                        if expected_year is not None and abs(box_year - expected_year) > 1:
                            logger.warning(f"Year mismatch for box {i}: {box_year} (Expected: {expected_year}). Skipping.")
                            continue  # Skip this box if the year doesn't match

                        # After navigating to the movie details page and verifying the title/year
                        if prioritize_buttons_in_box(result_box):
                            logger.info(f"Successfully handled buttons in box {i}.")
                            confirmation_flag = True  # Mark confirmation as successful
                        else:
                            logger.warning(f"Failed to handle buttons in box {i}. Skipping.")
                        # After clicking, check if the button has changed to "RD (0%)" or "RD (100%)"
                        try:
                            rd_button = WebDriverWait(driver, 10).until(
                                EC.presence_of_element_located((By.XPATH, ".//button[contains(text(), 'RD (')]"))
                            )
                            rd_button_text = rd_button.text
                            logger.info(f"RD button text after clicking: {rd_button_text}")

                            # If the button is now "RD (0%)", undo the click and retry with the next box
                            if "RD (0%)" in rd_button_text:
                                logger.warning(f"RD (0%) button detected after clicking Instant RD in box {i} {title_text}. Undoing the click and moving to the next box.")
                                rd_button.click()  # Undo the click by clicking the RD (0%) button
                                continue  # Move to the next box

                            # If it's "RD (100%)", we are done with this entry
                            if "RD (100%)" in rd_button_text:
                                logger.success(f"RD (100%) button detected. {i} {title_text}. This entry is complete.")
                                confirmation_flag = True
                                return confirmation_flag  # Exit the function as we've found a matching red button
                                break  # Break out of the loop since the task is complete

                        except TimeoutException:
                            logger.warning(f"Timeout waiting for RD button status change in box {i}.")
                            continue  # Move to the next box if a timeout occurs

                    except NoSuchElementException as e:
                        logger.warning(f"Could not find 'Instant RD' button in box {i}: {e}")
                    except TimeoutException as e:
                        logger.warning(f"Timeout when processing box {i}: {e}")

            except TimeoutException:
                logger.warning("Timeout waiting for result boxes to appear.")

            return confirmation_flag  # Return the confirmation flag

        except TimeoutException:
            logger.warning("Timeout waiting for the RD status message.")
            return

    except Exception as ex:
        logger.critical(f"Error during Selenium automation: {ex}")

async def get_user_input():
    try:
        # Check if running in a Docker container or non-interactive environment
        if os.getenv("RUNNING_IN_DOCKER", "false").lower() == "true":
            print("Running in Docker, automatically selecting 'y' for recurring Overseerr check.")
            return 'y'  # Automatically return 'yes' when running in Docker

        # Simulate asynchronous input with a timeout for interactive environments
        user_input = await asyncio.wait_for(
            asyncio.to_thread(input, "Do you want to start the initial check and recurring task? (y/n): "), 
            timeout=10
        )
        return user_input.strip().lower()

    except asyncio.TimeoutError:
        print("Input timeout. Defaulting to 'n'.")
        return 'n'  # Default to 'no' if no input is provided within 10 seconds

    except EOFError:
        # Handle cases where no input is provided (e.g., non-interactive environment)
        print("No input received (EOFError). Defaulting to 'n'.")
        return 'n'

### Webhook Endpoint ###
@app.post("/jellyseer-webhook")
@app.post("/jellyseer-webhook/")
async def webhook_root(request: Request):
    """Root webhook handler that routes based on media type"""
    try:
        data = await request.json()
        media = data.get('media', {})
        media_type = media.get('mediaType')
        tmdb_id = media.get('tmdbId')
        tvdb_id = media.get('tvdbId')

        logger.info(f"Received webhook - Type: {media_type}, TMDB ID: {tmdb_id}, TVDB ID: {tvdb_id}")

        # Route based on presence of tvdbId or mediaType
        if tvdb_id or media_type == "tv":
            logger.info(f"Routing TV show request to tv_webhook handler")
            return await tv_webhook(request)
        else:
            logger.info(f"Routing movie request to movie_webhook handler")
            return await movie_webhook(request)

    except Exception as e:
        logger.error(f"Error in webhook_root: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/jellyseer-webhook/tv")
async def tv_webhook(request: Request):
    """Handle TV show requests using IMDB ID"""
    try:
        if isinstance(request, dict):
            data = request  # If called internally
        else:
            data = await request.json()  # If called via webhook
            
        tmdb_id = data.get('media', {}).get('tmdbId')
        logger.info(f"Processing TV show TMDB ID: {tmdb_id}")
        
        # Get IMDB ID from TMDB
        url = f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={TMDB_API_KEY}&append_to_response=external_ids"
        logger.info(f"Fetching TV show details from TMDB API: {url}")
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    show_data = await response.json()
                    imdb_id = show_data.get('external_ids', {}).get('imdb_id')
                    show_title = show_data.get('name', '')
                    
                    if not imdb_id:
                        logger.error(f"No IMDB ID found for show: {show_title}")
                        return {"status": "error", "message": "No IMDB ID found"}
                    
                    # Ensure IMDB ID has tt prefix
                    if not imdb_id.startswith('tt'):
                        imdb_id = f"tt{imdb_id}"
                    
                    logger.info(f"Found show: {show_title} with IMDB ID: {imdb_id}")
                    
                    # Construct and navigate to DMM URL
                    show_url = f"https://debridmediamanager.com/show/{imdb_id}/1"
                    logger.info(f"Navigating to DMM URL: {show_url}")
                    
                    driver.get(show_url)
                    
                    # Wait for the page to load
                    try:
                        WebDriverWait(driver, 15).until(
                            EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'border-black')]"))
                        )
                        logger.info("Page loaded, checking for result boxes...")
                        
                        # Find all result boxes
                        result_boxes = driver.find_elements(By.XPATH, "//div[contains(@class, 'border-black')]")
                        logger.info(f"Found {len(result_boxes)} result boxes")
                        
                        for i, result_box in enumerate(result_boxes, 1):
                            try:
                                # First check for existing RD (100%) button
                                try:
                                    rd_100_button = result_box.find_element(By.XPATH, ".//button[contains(@class, 'bg-red-900/30')]")
                                    logger.info(f"Found existing RD (100%) button in box {i}, skipping...")
                                    continue
                                except NoSuchElementException:
                                    pass  # No RD (100%) button found, continue with checks
                                
                                # Try Instant RD first
                                try:
                                    instant_rd_button = result_box.find_element(By.XPATH, ".//button[contains(text(), 'Instant RD')]")
                                    logger.info(f"Found Instant RD button in box {i}")
                                    
                                    # Get initial state
                                    initial_state = instant_rd_button.get_attribute("class")
                                    logger.info(f"Initial button state: {initial_state}")
                                    
                                    # Click the button
                                    instant_rd_button.click()
                                    logger.success(f"Clicked Instant RD button in box {i}")
                                    
                                    # Wait for state change
                                    WebDriverWait(result_box, 5).until(
                                        lambda x: instant_rd_button.get_attribute("class") != initial_state
                                    )
                                    logger.success(f"Button state changed in box {i}")
                                    return {"status": "success", "message": f"Processed {show_title}"}
                                    
                                except (NoSuchElementException, TimeoutException):
                                    # Try DL with RD if Instant RD fails
                                    try:
                                        dl_with_rd_button = result_box.find_element(By.XPATH, ".//button[contains(text(), 'DL with RD')]")
                                        logger.info(f"Found DL with RD button in box {i}")
                                        
                                        # Get initial state
                                        initial_state = dl_with_rd_button.get_attribute("class")
                                        logger.info(f"Initial button state: {initial_state}")
                                        
                                        # Click the button
                                        dl_with_rd_button.click()
                                        logger.success(f"Clicked DL with RD button in box {i}")
                                        
                                        # Wait for state change
                                        WebDriverWait(result_box, 5).until(
                                            lambda x: dl_with_rd_button.get_attribute("class") != initial_state
                                        )
                                        logger.success(f"Button state changed in box {i}")
                                        return {"status": "success", "message": f"Processed {show_title}"}
                                        
                                    except (NoSuchElementException, TimeoutException):
                                        logger.warning(f"No valid RD buttons found in box {i}")
                                        continue
                                
                            except Exception as e:
                                logger.warning(f"Error processing box {i}: {str(e)}")
                                continue
                        
                        logger.warning("No valid RD buttons found in any box")
                        return {"status": "error", "message": "No valid RD buttons found"}
                        
                    except TimeoutException:
                        logger.error("Timeout waiting for page to load")
                        return {"status": "error", "message": "Page load timeout"}
                        
                else:
                    logger.error(f"TMDB API request failed with status: {response.status}")
                    return {"status": "error", "message": "TMDB API request failed"}
                    
    except Exception as e:
        logger.error(f"Error processing TV show: {str(e)}")
        return {"status": "error", "message": str(e)}

@app.post("/jellyseer-webhook/movie")
async def movie_webhook(request: Request):
    """Handle movie requests"""
    try:
        data = await request.json()
        logger.info(f"Received movie webhook for TMDB ID: {data.get('media', {}).get('tmdbId')}")
        payload = WebhookPayload(**data)
        return await process_movie_request(payload)
    except Exception as e:
        logger.error(f"Error processing movie webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def get_tv_show_imdb_id(tmdb_id: int) -> Optional[dict]:
    """Get IMDB ID for TV show from TMDB API"""
    url = f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={TMDB_API_KEY}&append_to_response=external_ids"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    imdb_id = data.get('external_ids', {}).get('imdb_id')
                    
                    if not imdb_id:
                        logger.error(f"No IMDB ID found for TMDB ID: {tmdb_id}")
                        return None
                    
                    logger.info(f"Found IMDB ID {imdb_id} for show: {data['name']}")
                    return {
                        "imdb_id": imdb_id,
                        "title": data['name']
                    }
                    
                logger.error(f"TMDB API request failed with status: {response.status}")
                return None
                
    except Exception as e:
        logger.error(f"Error fetching TV show details: {e}")
        return None

def schedule_token_refresh():
    """Schedule the token refresh every 10 minutes."""
    scheduler.add_job(check_and_refresh_access_token, 'interval', minutes=10)
    logger.info("Scheduled token refresh every 10 minutes.")

### Background Task to Process Overseerr Requests Periodically ###
@app.on_event("startup")
async def startup_event():
    """Startup event handler"""
    try:
        logger.info("Starting SeerrBridge...")
        
        # Initialize the browser
        await initialize_browser()
        
        # Start the request processing task
        logger.info("Started request processing task.")
        
        # Schedule token refresh
        schedule_token_refresh()
        
        # Get user input for recurring check (or auto-yes in Docker)
        user_input = await get_user_input()
        
        if user_input.lower() == 'y':
            # Initial check of movie requests
            try:
                requests = await get_overseerr_media_requests()
                if requests:
                    await process_requests()
                logger.info("Completed initial check of movie requests.")
            except Exception as e:
                logger.error(f"Error while processing movie requests: {e}")
            
            # Schedule recurring checks
            schedule_recheck_movie_requests()
            
    except Exception as e:
        logger.error(f"Error in startup event: {e}")


def schedule_recheck_movie_requests():
    # Correctly schedule the job with the REFRESH_INTERVAL_MINUTES configured interval.
    scheduler.add_job(process_movie_requests, 'interval', minutes=REFRESH_INTERVAL_MINUTES)
    logger.info(f"Scheduled rechecking movie requests every {REFRESH_INTERVAL_MINUTES} minute(s).")



async def on_close():
    await shutdown_browser()  # Ensure browser is closed when the bot closes

async def search_tv_show(title: str, season: int, episode: int, driver) -> bool:
    try:
        # Clean and normalize the TV show title
        show_title_cleaned = clean_title(title)
        show_title_normalized = normalize_title(title)
        
        logger.info(f"Searching for TV show: {title} S{season:02d}E{episode:02d}")
        
        # Navigate to DMM search page
        driver.get("https://debridmediamanager.com")
        
        # Wait for search input and enter show title
        search_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//input[@type='search']"))
        )
        search_input.clear()
        search_input.send_keys(title)
        search_input.send_keys(Keys.RETURN)
        
        # Format season and episode pattern (e.g., S01E01)
        episode_pattern = f"S{season:02d}E{episode:02d}"
        
        # Wait for search results and find matching TV show episodes
        try:
            show_elements = WebDriverWait(driver, 10).until(
                EC.presence_of_all_elements_located((By.XPATH, f"//a[contains(@href, '/tv/')]"))
            )
            
            for show_element in show_elements:
                show_title_element = show_element.find_element(By.XPATH, ".//h3")
                show_title_text = show_title_element.text.strip()
                
                # Check if the element contains the correct season and episode
                if episode_pattern.lower() in show_title_text.lower():
                    title_match_ratio = fuzz.ratio(
                        normalize_title(show_title_text.split(episode_pattern)[0]).lower(),
                        show_title_normalized.lower()
                    )
                    
                    if title_match_ratio >= 69:
                        logger.info(f"Found matching episode: {show_title_text}")
                        show_element.click()
                        
                        # Process the episode similarly to movies
                        if await process_tv_episode(driver, title, season, episode):
                            return True
            
            logger.warning(f"No matching episode found for {title} {episode_pattern}")
            return False
            
        except TimeoutException:
            logger.error(f"Timeout while searching for TV show: {title} {episode_pattern}")
            return False
            
    except Exception as ex:
        logger.critical(f"Error processing TV show {title} S{season:02d}E{episode:02d}: {ex}")
        return False

async def process_tv_episode(driver, title: str, season: int, episode: int) -> bool:
    """
    Process a specific TV episode in DMM similar to how movies are processed
    """
    # Similar to the movie processing logic but adapted for TV episodes
    try:
        # Wait for the episode's details page to load
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//div[@role='status']"))
        )
        
        # Rest of the processing logic similar to movies
        # You can reuse much of the existing movie processing logic here
        return True
        
    except Exception as ex:
        logger.error(f"Error processing episode {title} S{season:02d}E{episode:02d}: {ex}")
        return False

# Main entry point for running the FastAPI server
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8777)
