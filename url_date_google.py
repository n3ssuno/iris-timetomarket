#!/usr/bin/env python

"""
Script useful to search a list of URLs on Google 
  and extract a plausible creation-date for each of them

Author: Carlo Bottai
Copyright (c) 2021 - TU/e and EPFL
License: See the LICENSE file.
Date: 2021-08-25

"""

import pandas as pd
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync
import requests
import os
import json
import re
from datetime import datetime
from tqdm import tqdm
from iris_utils.parse_args import parse_io


# -----------------

# Google selectors
GOOGLE_MAIN_SELECTOR = '#main'
GOOGLE_RESULTS_SELECTOR = (
    '//div['
    'contains(concat(" ", normalize-space(@class), " "), " mnr-c ") and '
    'descendant::div/@aria-label="About this Result"]')
GOOGLE_HREF_SELECTOR = re.sub(r'^//', '', GOOGLE_RESULTS_SELECTOR)
GOOGLE_HREF_SELECTOR = f'//a[ancestor::{GOOGLE_HREF_SELECTOR}]'

# Regex to extract a date from a text
# It searches substrings like "Jun 10, 2010" or "by J Doe · 2010"
DATE_RE = re.compile((
    r'(([A-Z][a-z]{2,} [0-9]{1,2},)|'
    r'(by [A-Z-]* [A-Za-z\s-]* ·)) [0-9]{4}'))

# Use the browser in headless mode
# NB If you set it to False, the script cannot save a PDF version of the visited websites
HEADLESS = True

# Set the navigation timeout to 5min for the browser (text extraction from the HTML documents)
TIMEOUT = 300000

# Use mobile version of the HTML documents?
USE_MOBILE = True

# Use proxy?
USE_PROXY = True

# Chromium browser configuration parameters
BROWSER_CONFIG = {
    'headless': HEADLESS,
    'args': [
        '--ignore-certificate-errors',
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-accelerated-2d-canvas',
        '--disable-gpu',
        '--window-position=0,0',
        '--start-fullscreen',
        '--hide-scrollbars']}


# -----------------

CLEAN_RE = re.compile(r'[^A-Za-z\s]+')
def clean_text(text):
    try:
        text = text.lower()
        text = CLEAN_RE.sub('', text)
    except:
        text = ''
    return text

ATTRIBUTION_RE = re.compile(r'by [A-Z-]* [A-Za-z\s-]* · ')
def canonicalization_date(date, formats = [r'%b %d, %Y', r'%B %d, %Y', r'%Y']):
    date = ATTRIBUTION_RE.sub('', date).strip()
    for format in formats:
        try:
            date = datetime.strptime(date, format)
            date = date.strftime(r'%Y-%m-%d')
            return date
        except:
            continue
    return date


# -----------------

def rotate_proxy(page, PROXY_CONFIG, proxy_status, idx = None, verbose = False):
    if idx == None or (idx != 0 and idx % 10 == 0):
        proxy_rotate = requests.get(PROXY_CONFIG['PROXY_ROTATE'])
        page.wait_for_timeout(10 * 1000)
        proxy_ok, proxy_msg = proxy_status(proxy_rotate.text)
        if proxy_ok:
            if verbose:
                print('PROXY STATUS: OK')
                print(proxy_msg)
            pass
        else:
            print(proxy_rotate.text)
    return None

def launch_browser(playwright, PROXY_CONFIG):
    if PROXY_CONFIG:
        PROXY_SERVER = f'{PROXY_CONFIG["PROXY_ADDRESS"]}:{PROXY_CONFIG["PROXY_PORT"]}'
        BROWSER_CONFIG['proxy'] = {
            'server': PROXY_SERVER,
            'username': PROXY_CONFIG['PROXY_USER'],
            'password': PROXY_CONFIG['PROXY_PASSWORD']}
    
    # Launch the browser
    browser = playwright \
        .chromium.launch(**BROWSER_CONFIG)
    
    # Simulate a Nexus 10 smartphone
    # TODO The script is supposed to be used with both the mobile and desktop 
    #   version of the browser (this is why the variable USE_MOBILE exists)
    #   However, only the use of the mobile version has been implemented for now
    if USE_MOBILE:
        nexus10 = playwright.devices['Nexus 10']
        browser_context = browser.new_context(**nexus10)

    # Set the timeout chosen
    browser_context.set_default_timeout(TIMEOUT)

    # Open new page
    page = browser_context.new_page()
    
    # Set the page in "stealth mode"
    stealth_sync(page)

    return browser, browser_context, page

def accept_cookies(page):
    try:
        page.click(
            'button:has-text("I agree")', 
            timeout = 30 * 1000)
    except:
        pass

def search_on_google(page, query):
    # Click on the search field
    page.click('[aria-label="Search"]')
    
    # Delete the content in the search field
    page.press('[aria-label="Search"]', 'Control+A') # page.keyboard.press('Control+A')
    page.press('[aria-label="Search"]', 'Delete') # page.keyboard.press('Delete')

    # Fill the search field with the query of interest
    page.type('[aria-label="Search"]', query, delay=50)
    page.wait_for_timeout(1 * 1000)

    # Press Enter
    with page.expect_navigation():
        page.press('[aria-label="Search"]', 'Enter')
    page.wait_for_timeout(2 * 1000)

    # Wait until the Google results page hasn't been generated
    page.wait_for_selector(GOOGLE_MAIN_SELECTOR)

def detected(page):
    if page.url.startswith('https://www.google.com/sorry/'):
        return True
    return False

def nothing_found(page):
    page.wait_for_selector('#topstuff', state = 'attached')
    no_results_top = page.inner_text('#topstuff')
    #no_results_bot = page.inner_text('#botstuff')
    no_results_msgs = [no_results_top] # [no_results_top, no_results_bot]
    no_results_msgs = [clean_text(no_results_msg) \
        for no_results_msg in no_results_msgs]
    no_results_targets = [
        'no results found for', 
        'did not match any documents']
        # 'get the answer youre looking for added to the web'
    return any([no_results_msg.find(no_results_target) >= 0 \
            for no_results_target in no_results_targets \
                for no_results_msg in no_results_msgs])

def extract_information_from_results(page):
    page.wait_for_selector('#rso')
    
    first_result_text = page.inner_text(GOOGLE_RESULTS_SELECTOR)
    first_result_date = DATE_RE.search(first_result_text)
    first_result_href = page.eval_on_selector(
        GOOGLE_HREF_SELECTOR, 'el => el.href')
    
    try:
        first_result_date = first_result_date.group()
    except:
        first_result_date = 'NO_DATE_DETECTED'
    else:
        # Clean the date to a %Y-%m-%d format
        first_result_date = canonicalization_date(first_result_date)
    
    return first_result_date, first_result_href

def comply_with_terms_of_use(page, PROXY_CONFIG):
    if PROXY_CONFIG:
        waiting_time = 60
    else:
        waiting_time = 180
    page.wait_for_timeout(waiting_time * 1000)

def write_results(input_data, output_info, output_file):
    with open(output_file, 'a') as f_out:
        f_out.write((
            f'{input_data["url_id"]}\t'
            f'{input_data["url"]}\t'
            f'{output_info["date_url"]}\t'
            f'{output_info["dated_url"]}\n'))

def handle_errors(element):
    print(f'Error while analyzing {element}')
    return 'ERROR', 'ERROR'


# -----------------

def run_scraper(data, playwright, output_file, PROXY_CONFIG = None):
    # Launch the browser and open a new page
    browser, browser_context, page = launch_browser(
        playwright, PROXY_CONFIG)

    # Start the proxy if it is in use
    if PROXY_CONFIG:
        proxy_status = eval(PROXY_CONFIG['PROXY_STATUS'])
        rotate_proxy(page, PROXY_CONFIG, proxy_status, verbose = True)
    
    # Go to https://www.google.com/?gl=us
    page.goto((
        'https://www.google.com/?gl=us&'
        'tbs=cdr%3A1%2Ccd_min%3A1994%2Ccd_max%3A2021'))

    # Accept cookies policy
    accept_cookies(page)

    for idx, row in tqdm(data.iterrows(), tot = len(data)):
        # Rotate the proxy if it is in use
        if PROXY_CONFIG:
            rotate_proxy(page, PROXY_CONFIG, proxy_status, idx = idx)

        try:
            # Search the URL of interest on Google
            search_on_google(page, row['url'])
        
            # Check if the scraper is in the results page, as expected, 
            #   and if the scraper has not been detected by Google
            try:
                assert page.title().find('Google Search') >= 0
                assert not detected(page)
            except:
                print('Scraper detected by Google')
                print('Wait for 2h and try again')
                page.wait_for_timeout(60 * 60 * 2 * 1000)
                first_result_date = 'SCRAPER_DETECTED'
                first_result_href = 'SCRAPER_DETECTED'
            else:
                # Check if no results have been found
                if nothing_found(page):
                    first_result_date = 'NO_RESULTS'
                    first_result_href = 'NO_RESULTS'
                else:
                    # Select the first result and extract the first substring 
                    #   that resembles a date and the URL of the result 
                    #   actually found by Google
                    try:
                        first_result_date, first_result_href = \
                            extract_information_from_results(page)
                    except:
                        first_result_date, first_result_href = handle_errors(row['url'])
        except:
            first_result_date, first_result_href = handle_errors(row['url'])

        finally:
            # Write a line in the results
            output_info = {
                'date_url': first_result_date, 
                'dated_url': first_result_href}
            write_results(row, output_info, output_file)
            

            # Wait for some minutes to comply with Google's terms of use
            comply_with_terms_of_use(page, PROXY_CONFIG)
    
    # Close page
    page.close()

    browser_context.close()
    browser.close()


# -----------------

def main():
    args = parse_io()

    # The data frame must contain two columns:
    #   * url_id (unique identifier of a URL; useful to merge back the output with the main dataframe)
    #   * url    (the URL itself)
    df = args.input_list[0]
    
    if not os.path.exists(args.output):
        with open(args.output, 'w') as f_out:
            f_out.write('id\turl\tdate_url\tdated_url\n')
    else:
        df_bak = pd.read_table(args.output)
        if len(df_bak):
            df = df[~df['url'].isin(df_bak['url'])]
    
    PROXY_CONFIG = None
    if USE_PROXY:
        try:
            with open(args.input_list[2], 'r') as f_in: # 'proxy.conf'
                PROXY_CONFIG = json.load(f_in)
        except:
            pass
    
    with sync_playwright() as playwright:
        run_scraper(df, playwright, args.output, PROXY_CONFIG)


if __name__ == '__main__':
    main()
