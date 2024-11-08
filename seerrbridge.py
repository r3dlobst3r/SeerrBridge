# =============================================================================
# Soluify.com  |  Your #1 IT Problem Solver  |  {SeerrBridge v0.2.2}
# =============================================================================
#  __         _
# (_  _ |   .(_
# __)(_)||_||| \/
#              /
# © 2024
# -----------------------------------------------------------------------------
import discord
import asyncio
import json
import time
import os
import sys
import urllib.parse
import re
import inflect
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from asyncio import Queue
from datetime import datetime, timedelta
from discord.ext import tasks
from deep_translator import GoogleTranslator
from fuzzywuzzy import fuzz
from loguru import logger


# Configure loguru
logger.remove()  # Remove default handler
logger.add("seerbridge.log", rotation="500 MB", encoding='utf-8')  # Use utf-8 encoding for log file
logger.add(sys.stdout, colorize=True)  # Ensure stdout can handle Unicode
logger.level("WARNING", color="<cyan>")

# Load environment variables
load_dotenv()

TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = os.getenv('DISCORD_CHANNEL_ID')
try:
    CHANNEL_ID = int(CHANNEL_ID)
except ValueError:
    logger.error("DISCORD_CHANNEL_ID must be an integer.")
    exit(1)

# Securely load credentials from environment variables
RD_ACCESS_TOKEN = os.getenv('RD_ACCESS_TOKEN')

# Discord bot intents
intents = discord.Intents.default()
intents.messages = True  # Enable message intent
intents.guilds = True
intents.message_content = True  # Ensure message content intent is enabled

client = discord.Client(intents=intents)

# Global driver variable to hold the Selenium WebDriver
driver = None

# Initialize a global queue with a maximum size of 500
request_queue = Queue(maxsize=500)
processing_task = None  # To track the current processing task

### Helper function to handle login
def login(driver):
    logger.info("Initiating login process.")

    try:
        # Click the "Login with Real Debrid" button
        login_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Login with Real Debrid')]"))
        )
        login_button.click()
        logger.info("Clicked on 'Login with Real Debrid' button.")

    except (TimeoutException, NoSuchElementException) as ex:
        logger.error(f"Error during login process: {ex}")


### Browser Initialization and Persistent Session
async def initialize_browser():
    global driver
    if driver is None:
        logger.info("Starting persistent browser session.")

        options = Options()
        options.add_argument('--headless')  # Run browser in headless mode
        options.add_argument('--disable-gpu')  # Disable GPU to save resources

        chromedriver_path = os.getenv('CHROMEDRIVER_PATH')
        service = ChromeService(executable_path=chromedriver_path)
        driver = webdriver.Chrome(service=service, options=options)
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
            Object.defineProperty(navigator, 'webdriver', {
              get: () => undefined
            })
          """
        })
        logger.success("Initialized Selenium WebDriver.")

        # Navigate to Debrid Media Manager
        driver.get("https://debridmediamanager.com")
        logger.success("Navigated to Debrid Media Manager start page.")

        # Inject Real-Debrid access token and other credentials into local storage
        driver.execute_script(f"""
            localStorage.setItem('rd:accessToken', '{RD_ACCESS_TOKEN}');
        """)
        logger.info("Set Real-Debrid credentials in local storage.")

        # Refresh the page to apply the local storage values
        driver.refresh()
        logger.success("Refreshed the page to apply local storage values.")
        # After refreshing, call the login function to click the login button
        login(driver)
        # After successful login, click on "⚙️ Settings" to open the settings popup
        try:

            logger.info("Attempting to click the '⚙️ Settings' link.")
            settings_link = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//a[contains(text(),'⚙️ Settings')]"))
            )
            settings_link.click()
            logger.info("Clicked on '⚙️ Settings' link.")
            # Wait for the settings popup to appear
            WebDriverWait(driver, 10).until(

                EC.presence_of_element_located((By.XPATH, "//h2[contains(text(),'⚙️ Settings')]"))
            )

            logger.info("Settings popup appeared.")
            # Locate the "Default torrents filter" input box and insert the regex
            logger.info("Attempting to insert regex into 'Default torrents filter' box.")
            default_filter_input = WebDriverWait(driver, 10).until(

                EC.presence_of_element_located((By.ID, "dmm-default-torrents-filter"))
            )
            default_filter_input.clear()  # Clear any existing filter

            # Add custom Regex here
            default_filter_input.send_keys("^(?!.*【.*?】)(?!.*[\u0400-\u04FF])(?!.*\[esp\]).* videos:1")

            logger.info("Inserted regex into 'Default torrents filter' input box.")

            # Confirm the changes by clicking the 'OK' button on the popup
            save_button = WebDriverWait(driver, 10).until(

                EC.element_to_be_clickable((By.XPATH, "//button[@class='swal2-confirm !bg-blue-600 !px-6 haptic swal2-styled']"))
            )
            save_button.click()
            logger.success("Clicked 'Save' to save settings.")

        except (TimeoutException, NoSuchElementException) as ex:

            logger.error(f"Error while interacting with the settings popup: {ex}")

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
    while True:
        movie_title = await request_queue.get()  # Wait for the next request in the queue
        logger.info(f"Processing movie request: {movie_title}")
        try:
            await asyncio.to_thread(search_on_debrid, movie_title, driver)  # Process the request
        except Exception as ex:
            logger.critical(f"Error processing movie request {movie_title}: {ex}")
        finally:
            request_queue.task_done()  # Mark the request as done

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

### Updated Function to Fetch Messages from the Last 30 Days (Newest First)
async def fetch_last_30_days_messages():
    logger.info("Fetching messages from the last 30 days...")
    channel = client.get_channel(CHANNEL_ID)
    
    if not channel:
        logger.error(f"Channel with ID {CHANNEL_ID} not found.")
        return []
    
    # Calculate the timestamp for 30 days ago
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    
    # List to store fetched messages
    messages = []
    
    try:
        # Asynchronously iterate over the message history from newest to oldest
        async for message in channel.history(after=thirty_days_ago, limit=None):
            messages.append(message)
        
        logger.info(f"Fetched {len(messages)} messages from the last 30 days.")
        return messages  # Messages are already in newest-to-oldest order
    except Exception as e:
        logger.error(f"Error fetching messages: {e}")
        return []

### Process the fetched messages (newest to oldest)
async def process_movie_requests():
    messages = await fetch_last_30_days_messages()
    if not messages:
        return
    
    # Dictionary to store movie requests
    movie_requests = {}
    
    # Step 1: Parse messages for movie requests (newest to oldest)
    for message in messages:
        if message.embeds:
            for embed in message.embeds:
                if embed.author and embed.author.name == "Movie Request Automatically Approved":
                    movie_title = embed.title.strip() if embed.title else None
                    if movie_title:
                        logger.info(f"Found movie request: {movie_title}")
                        movie_requests[movie_title] = False  # Initially mark as not available
    
    # Step 2: Check if the movie has been marked as "available"
    for message in messages:
        if message.embeds:
            for embed in message.embeds:
                if embed.author and embed.author.name == "Movie Request Now Available":
                    movie_title = embed.title.strip() if embed.title else None
                    if movie_title and movie_title in movie_requests:
                        logger.info(f"Movie found as available: {movie_title}")
                        movie_requests[movie_title] = True  # Mark as available
    
    # Step 3: Re-add unavailable movies to the queue (newest to oldest)
    for movie_title, available in movie_requests.items():
        if not available:
            logger.info(f"Movie {movie_title} is not available, re-adding to the queue.")
            success = await add_request_to_queue(movie_title)
            if not success:
                logger.warning(f"Failed to re-add {movie_title} to the queue.")


### Task to Recheck Every X Hours
@tasks.loop(hours=2)
async def recheck_movie_requests():
    logger.info("Rechecking movie requests from the last 30 days...")
    await process_movie_requests()

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
        WebDriverWait(driver, 10).until(
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
                if title_match_ratio >= 85 and (expected_year is None or abs(search_year - expected_year) <= 1):
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

                # Iterate through each red button and its associated title
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

                        # Define the current year for comparison
                        current_year = datetime.now().year

                        # Compare the title in various forms (cleaned, normalized, etc.)
                        if (
                            red_button_title_cleaned.startswith(movie_title_cleaned) or
                            red_button_title_normalized.startswith(movie_title_normalized)
                        ):
                            # Check if the years match or if the expected year is within ±1 year range
                            if expected_year is None or abs(red_button_year - expected_year) <= 1:
                                # Title matches, skip further action for this entry
                                logger.warning(f"Title matches with red button {i}: {red_button_title_cleaned}. Skipping this entry.")
                                return  # Exit the function as we've found a matching red button
                            else:
                                logger.warning(f"Year mismatch with red button {i}: {red_button_year} (Expected: {expected_year}). Skipping.")
                                continue
                        else:
                            # Title does not match, log and continue to the next red button
                            logger.info(f"Title mismatch with red button {i}: {red_button_title_cleaned}. Continuing to check other boxes.")
                    
                    except NoSuchElementException as e:
                        logger.warning(f"Could not find title associated with red button {i}: {e}")
                        continue  # If a title is not found for a red button, continue to the next one

            except NoSuchElementException:
                logger.info("No red buttons (100% RD) detected. Proceeding with 'Checking RD availability...'.")

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

                # Iterate through each red button and its associated title
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

                        # Define the current year for comparison
                        current_year = datetime.now().year

                        # Compare the title in various forms (cleaned, normalized, etc.)
                        if (
                            red_button_title_cleaned.startswith(movie_title_cleaned) or
                            red_button_title_normalized.startswith(movie_title_normalized)
                        ):
                            # Check if the years match or if the expected year is within ±1 year range
                            if expected_year is None or abs(red_button_year - expected_year) <= 1:
                                # Title matches, skip further action for this entry
                                logger.warning(f"Title matches with red button {i}: {red_button_title_cleaned}. Skipping this entry.")
                                return  # Exit the function as we've found a matching red button
                            else:
                                logger.warning(f"Year mismatch with red button {i}: {red_button_year} (Expected: {expected_year}). Skipping.")
                                continue
                        else:
                            # Title does not match, log and continue to the next red button
                            logger.info(f"Title mismatch with red button {i}: {red_button_title_cleaned}. Continuing to check other boxes.")
                    
                    except NoSuchElementException as e:
                        logger.warning(f"Could not find title associated with red button {i}: {e}")
                        continue  # If a title is not found for a red button, continue to the next one

            except NoSuchElementException:
                logger.info("No red buttons (100% RD) detected. Proceeding with 'Checking RD availability...'.")

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
                        # Compare the title in all variations
                        if not (
                            title_text_cleaned.startswith(movie_title_cleaned) or
                            title_text_normalized.startswith(movie_title_normalized) or
                            title_text_cleaned_word.startswith(movie_title_cleaned_word) or
                            title_text_normalized_word.startswith(movie_title_normalized_word) or
                            title_text_cleaned_digit.startswith(movie_title_cleaned_digit) or
                            title_text_normalized_digit.startswith(movie_title_normalized_digit)
                        ):
                            logger.warning(f"Title mismatch for box {i}: {title_text_cleaned} or {title_text_normalized} (Expected: {movie_title_cleaned} or {movie_title_normalized}). Skipping.")
                            continue  # Skip this box if none of the variations match


                        # Compare the year with the expected year (allow ±1 year)
                        expected_year = extract_year(movie_title)
                        if expected_year is not None and abs(box_year - expected_year) > 1:
                            logger.warning(f"Year mismatch for box {i}: {box_year} (Expected: {expected_year}). Skipping.")
                            continue  # Skip this box if the year doesn't match

                        # Try to locate the Instant RD button using the button's class
                        instant_rd_button = result_box.find_element(By.XPATH, ".//button[contains(@class, 'bg-green-900/30')]")
                        instant_rd_button.click()
                        logger.success(f"Clicked 'Instant RD' in box {i} for {title_text} ({box_year}).")
                        time.sleep(2)

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



        except TimeoutException:
            logger.warning("Timeout waiting for the RD status message.")
            return


    except Exception as ex:
        logger.critical(f"Error during Selenium automation: {ex}")


### Discord Bot Logic
@client.event
async def on_ready():
    global processing_task
    logger.info(f'Bot logged in as {client.user}')
    
    # Always initialize the browser when the bot is ready
    await initialize_browser()

    # Start the request processing task if not already started
    if processing_task is None:
        processing_task = asyncio.create_task(process_requests())
        logger.info("Started request processing task.")
    
    # Ask user if they want to proceed with the last part
    user_input = input("Do you want to start the initial check and recurring task? (y/n): ").strip().lower()

    if user_input == 'y':
        # Start the initial check for the last 30 days
        await process_movie_requests()

        # Start the recurring task (every X hours)
        recheck_movie_requests.start()
        logger.success(f"Started recurring task to recheck movie requests every {recheck_movie_requests.hours} hours. Running first check now.")
    
    elif user_input == 'n':
        logger.info("Initial check and recurring task were skipped by user input.")
    
    else:
        logger.warning("Invalid input. Please restart the bot and enter 'y' or 'n'.")


@client.event
async def on_message(message):
    logger.info(f"Message received in channel {message.channel.id}")

    if message.author == client.user:
        return

    if message.channel.id == CHANNEL_ID:
        logger.info("Message is in the correct channel.")

        if message.embeds:
            logger.info("Message contains embeds. Processing embeds...")

            for embed in message.embeds:
                logger.info(f"Embed title: {embed.title}")
                logger.info(f"Embed description: {embed.description}")

                if embed.author and embed.author.name == "Movie Request Automatically Approved":
                    logger.info(f"Detected a movie request from author: {embed.author.name}")

                    movie_title = embed.title.strip() if embed.title else None

                    if movie_title:
                        logger.info(f"New movie request detected: {movie_title}")
                        success = await add_request_to_queue(movie_title)  # Add request to the queue
                        if not success:
                            await message.channel.send("Request queue is full. Please try again later.")
                    else:
                        logger.warning("Embed title is missing. Skipping.")

@client.event
async def on_disconnect():
    logger.warning("Bot disconnected, attempting to reconnect...")


@client.event
async def on_close():
    await shutdown_browser()  # Ensure browser is closed when the bot closes

# Run the Discord client
if __name__ == "__main__":
    try:
        logger.info("Starting Discord bot...")
        client.run(TOKEN)
    except Exception as e:
        logger.critical(f"Failed to run the Discord bot: {e}")
        asyncio.run(shutdown_browser())  # Ensure browser is closed on critical failure
