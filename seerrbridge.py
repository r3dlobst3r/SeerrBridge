# =============================================================================
# Soluify.com  |  Your #1 IT Problem Solver  |  {SeerrBridge v0.1.1}
# =============================================================================
#  __         _
# (_  _ |   .(_
# __)(_)||_||| \/
#              /
# © 2024
# -----------------------------------------------------------------------------
import discord
import asyncio
import time
import os
import logging
import sys
import urllib.parse
import re
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from asyncio import Queue

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s:%(name)s: %(message)s',
    handlers=[
        logging.FileHandler("seerbridge.log", encoding='utf-8'),  # Use utf-8 encoding for log file
        logging.StreamHandler(sys.stdout)  # Ensure stdout can handle Unicode
    ]
)
logger = logging.getLogger(__name__)

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
REAL_DEBRID_USERNAME = os.getenv('REAL_DEBRID_USERNAME')
REAL_DEBRID_PASSWORD = os.getenv('REAL_DEBRID_PASSWORD')

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

        # Click the "Authorize Debrid Media Manager" button
        authorize_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Authorize Debrid Media Manager')]"))
        )
        authorize_button.click()
        logger.info("Clicked on 'Authorize Debrid Media Manager' button.")

        # Switch to the new tab for Real-Debrid login
        driver.switch_to.window(driver.window_handles[-1])

        # Fill in the username and password fields
        username_field = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "u"))
        )
        username_field.send_keys(REAL_DEBRID_USERNAME)
        logger.info("Entered username.")

        password_field = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "p"))
        )
        password_field.send_keys(REAL_DEBRID_PASSWORD)
        logger.info("Entered password.")

        # Submit the login form
        submit_button = driver.find_element(By.XPATH, "//input[@type='submit' and @value='Login']")
        time.sleep(2)
        submit_button.click()
        logger.info("Submitted the login form.")

        # Wait for the "Application allowed" message
        WebDriverWait(driver, 30).until(
            EC.text_to_be_present_in_element((By.XPATH, "//body"), "Application allowed, you can close this page")
        )
        logger.info("Login successful, authorization granted.")
        time.sleep(2)
        # Close the authorization tab and switch back to the debrid media manager tab
        driver.switch_to.window(driver.window_handles[0])

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
        logger.info("Initialized Selenium WebDriver.")

        # Perform login once during browser initialization
        driver.get("https://debridmediamanager.com")
        logger.info("Navigated to Debrid Media Manager start page.")
        login(driver)
        logger.info("Logged into Debrid Media Manager.")

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
            default_filter_input.send_keys("^(?!.*【.*?】)(?!.*[\u0400-\u04FF])(?!.*\[esp\]).*")
            logger.info("Inserted regex into 'Default torrents filter' input box.")

            # Confirm the changes by clicking the 'OK' button on the popup
            ok_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@class='swal2-confirm swal2-styled']"))
            )
            ok_button.click()
            logger.info("Clicked 'OK' to save settings.")

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
            logger.info("Library section loaded successfully.")
        except TimeoutException:
            logger.error("Library section did not load within the expected time.")

        # Wait for at least 2 seconds on the library page
        logger.info("Waiting for 2 seconds on the library page.")
        time.sleep(2)
        logger.info("Completed waiting on the library page.")

async def shutdown_browser():
    global driver
    if driver:
        driver.quit()
        logger.info("Selenium WebDriver closed.")
        driver = None

### Function to process requests from the queue
async def process_requests():
    while True:
        movie_title = await request_queue.get()  # Wait for the next request in the queue
        logger.info(f"Processing movie request: {movie_title}")
        try:
            await asyncio.to_thread(search_on_debrid, movie_title, driver)  # Process the request
        except Exception as ex:
            logger.error(f"Error processing movie request {movie_title}: {ex}")
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
def extract_year(text):
    match = re.search(r'\d{4}', text)
    if match:
        return int(match.group(0))
    return None


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
        logger.info(f"Navigated to search results page for {movie_title}.")
        time.sleep(3)  # Allow time for search results to load

        # Clean the movie title (remove year in parentheses)
        movie_title_cleaned = movie_title.split('(')[0].strip()
        logger.info(f"Searching for movie title: {movie_title_cleaned}")

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
                search_year = extract_year(movie_year_element.text)

                # Extract the expected year from the provided movie title (if it exists)
                expected_year = extract_year(movie_title)

                # Check if the titles match and if the years are within ±1 year range
                if search_title_cleaned.lower() == movie_title_cleaned.lower() and \
                   (expected_year is None or abs(search_year - expected_year) <= 1):
                    logger.info(f"Found matching movie: {search_title_cleaned} ({search_year})")
                    
                    # Click on the parent <a> tag (which is the clickable link)
                    parent_link = movie_element
                    parent_link.click()
                    logger.info(f"Clicked on the movie link for {search_title_cleaned}")
                    break
            else:
                logger.error(f"No matching movie found for {movie_title_cleaned} ({expected_year})")
                return
        except (TimeoutException, NoSuchElementException) as e:
            logger.error(f"Failed to find or click on the search result: {movie_title}")
            return

        # Wait for the movie's details page to load by listening for the status message
        try:
            # Step 1: Wait for the "Checking RD availability..." message to appear
            logger.info("Waiting for 'Checking RD availability...' to appear.")
            
            WebDriverWait(driver, 60).until(
                EC.text_to_be_present_in_element(
                    (By.XPATH, "//div[@role='status' and contains(@aria-live, 'polite')]"),
                    "Checking RD availability"
                )
            )
            
            logger.info("'Checking RD availability...' has appeared. Waiting for it to disappear & Checking if already available.")
            
            # Step 2: Check if any red button (RD 100%) exists immediately, and skip if found
            try:
                red_button = driver.find_element(By.XPATH, "//button[contains(@class, 'bg-red-500')]")
                logger.info("Red button (100% RD) detected. Skipping this entry.")
                return  # Skip this entry if the red button is detected
            except NoSuchElementException:
                logger.info("No red button (100% RD) detected. Proceeding with 'Checking RD availability...'.")

            # Step 3: Wait for the "Checking RD availability..." message to disappear
            try:
                WebDriverWait(driver, 60).until_not(
                    EC.text_to_be_present_in_element(
                        (By.XPATH, "//div[@role='status' and contains(@aria-live, 'polite')]"),
                        "Checking RD availability"
                    )
                )
                logger.info("'Checking RD availability...' has disappeared. Now waiting for RD results.")
            except TimeoutException:
                logger.warning("'Checking RD availability...' did not disappear within 60 seconds. Proceeding to the next steps.")

            # Step 4: Wait for the "Found X available torrents in RD" message
            status_element = WebDriverWait(driver, 60).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//div[@role='status' and contains(@aria-live, 'polite') and contains(text(), 'available torrents in RD')]")
                )
            )

            status_text = status_element.text
            logger.info(f"Status message: {status_text}")

            # Step 5: Extract the number of available torrents from the status message (look for the number)
            torrents_match = re.search(r"Found (\d+) available torrents in RD", status_text)

            if torrents_match:
                torrents_count = int(torrents_match.group(1))
                logger.info(f"Found {torrents_count} available torrents in RD.")

                # Step 6: If the status says "0 torrents", check if there's still an Instant RD button
                if torrents_count == 0:
                    logger.info("No torrents found in RD according to status, but checking for Instant RD buttons.")
                else:
                    logger.info(f"{torrents_count} torrents found in RD. Proceeding with RD checks.")
            else:
                logger.warning("Could not find the expected 'Found X available torrents in RD' message. Proceeding to check for Instant RD.")
            
            # Step 7: Check if any red button (RD 100%) exists again before continuing
            try:
                red_button = driver.find_element(By.XPATH, "//button[contains(@class, 'bg-red-500')]")
                logger.info("Red button (100% RD) detected after RD check. Skipping this entry.")
                return  # Skip this entry if the red button is detected
            except NoSuchElementException:
                logger.info("No red button (100% RD) detected. Proceeding with Instant RD check.")

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

                        # Clean the movie title and handle dots as spaces for comparison
                        movie_title_cleaned = movie_title.split('(')[0].strip().replace(' ', '.').lower()
                        title_text_cleaned = re.sub(r'\s+', '.', title_text.split(str(box_year))[0].strip().lower())

                        # Compare the title (allowing dots or spaces) and check if the year matches
                        if not title_text_cleaned.startswith(movie_title_cleaned):
                            logger.info(f"Title mismatch for box {i}: {title_text_cleaned} (Expected: {movie_title_cleaned}). Skipping.")
                            continue  # Skip this box if the title doesn't match

                        # Compare the year with the expected year (allow ±1 year)
                        expected_year = extract_year(movie_title)
                        if expected_year is not None and abs(box_year - expected_year) > 1:
                            logger.info(f"Year mismatch for box {i}: {box_year} (Expected: {expected_year}). Skipping.")
                            continue  # Skip this box if the year doesn't match

                        # Check the file count (ensure it's 1 folder)
                        file_info_element = result_box.find_element(By.XPATH, ".//div[contains(@class, 'text-gray-300')]")
                        file_info_text = file_info_element.text
                        file_count_match = re.search(r"\((\d+) 📂\)", file_info_text)

                        if file_count_match:
                            file_count = int(file_count_match.group(1))
                            if file_count > 1:
                                logger.info(f"Box {i} has {file_count} files (Expected: 1). Skipping.")
                                continue  # Skip this box if there is more than 1 file
                        else:
                            logger.warning(f"Could not extract file count from '{file_info_text}' in box {i}. Skipping.")
                            continue

                        # Try to locate the Instant RD button using the button's class
                        instant_rd_button = result_box.find_element(By.XPATH, ".//button[contains(@class, 'bg-green-600')]")
                        instant_rd_button.click()
                        logger.info(f"Clicked 'Instant RD' in box {i} for {title_text} ({box_year}).")
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
                                logger.info(f"RD (100%) button detected. {i} {title_text}. This entry is complete.")
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
        logger.error(f"Error during Selenium automation: {ex}")


### Discord Bot Logic
@client.event
async def on_ready():
    global processing_task
    logger.info(f'Bot logged in as {client.user}')
    await initialize_browser()  # Initialize the browser when the bot is ready
    
    if processing_task is None:  # Start the request processing task if not already started
        processing_task = asyncio.create_task(process_requests())
        logger.info("Started request processing task.")

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
