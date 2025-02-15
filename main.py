import os
import pathlib
import pdb
import re
import sys
import time
from datetime import datetime
from inspect import getsourcefile

import yaml
from PyPDF2 import PdfFileMerger
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

try:
    from yaml import CLoader as Loader
except ImportError:
    from yaml import Loader

def num_months(m1: datetime, m2: datetime):
    return (m1.year - m2.year) * 12 + m1.month - m2.month

def process_config(config):
    for entry in config['extraction']:
        entry['months'] = range(*(num_months(datetime.now(), datetime.strptime(x, "%m/%Y")) for x in (entry['from'], entry['to'])), -1)

def resolve_env():
    """ Adds the web drivers necessary for Selenium to work at runtime """
    
    basepath = f"{os.path.dirname(os.path.abspath(getsourcefile(lambda:0)))}/drivers"

    if any([sys.platform.startswith(x) for x in ['freebsd', 'linux', 'aix']]):
        # Linux
        os.environ['PATH'] += f":{basepath}/linux/"
    elif sys.platform.startswith('darwin'):
        # Unix
        os.environ['PATH'] += f":{basepath}/macos/"
    elif sys.platform.startswith('win'):
        # Windows
        os.environ['PATH'] += f";{basepath}/windows/"

def configure():
    """ Configures the driver with the correct options """
    conf = {}

    opt = webdriver.firefox.options.Options()
    opt.headless = True
    prof = webdriver.FirefoxProfile()

    prof.set_preference('browser.download.folderList', 2)
    prof.set_preference('browser.download.manager.showWhenStarting', False)
    prof.set_preference('browser.download.dir', os.getcwd())
    prof.set_preference('browser.helperApps.neverAsk.saveToDisk', 'application/pdf')
    prof.set_preference('pdfjs.disabled', True)
    prof.set_preference('plugin.scan.plid.all', False)
    prof.set_preference('plugin.scan.Acrobat', "99.0")
    prof.set_preference('general.warnOnAboutConfig', False)
    prof.update_preferences()

    conf['firefox_profile'] = prof

    conf['options'] = opt

    return conf

def login(driver, ssn: str = ""):
    """ Navigates the user to DNB and logs them in using a PIN and OTP combo and waits for the content to load """

    print("Logging in")

    driver.get("https://dnb.no")

    # Remove the modal block that may appear
    if driver.find_element_by_id('consent-modal').is_displayed():
        driver.find_element_by_id('consent-x').click()

    # DNB has two stages of login
    # The first one is simply entering a user's SSN
    # Then the user has to select the login type
    form_1 = driver.find_element_by_xpath("//form[@id='loginForm']")
    inp = form_1.find_element_by_xpath(".//input[@name='uid']")
    cnf = form_1.find_element_by_xpath(".//input[@id='loginFormSubmit'] | .//input[@name='Login']")

    if not ssn:
        ssn = input("Please enter your SSN for DNB: ")
    inp.clear()
    inp.send_keys(ssn)
    cnf.click()

    # Wait for the necessary DOM elements to be loaded
    WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.ID, "r_state-2")))

    # Select the easier method of logging in and logging in
    nd_login = driver.find_element_by_xpath("//div[@id='r_state-2']")
    nd_login.find_element_by_xpath("./div[1]").click()

    # Locate all the neccesary fields to log in with a PIN and OTP combo
    form_2 = nd_login.find_element_by_xpath("./div[2]//form")
    pin = form_2.find_element_by_xpath(".//input[@id='phoneCode']")
    otp = form_2.find_element_by_xpath(".//input[@id='otpCode']")
    btn = form_2.find_element_by_xpath(".//button")

    # Clear the fields and ask for user input
    pin.clear()
    otp.clear()
    pin.send_keys(input("Please enter your PIN: "))
    otp.send_keys(input("Please enter your one time password: "))

    # Login
    btn.click()

    # Wait for AJAX request to finish so that the required elements are present
    WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.ID, "gllwg04e")))

def navigate(driver):
    """ navigate to the correct part of the DNB website """

    print("Navigating")

    top_menu = driver.find_element_by_xpath("//div[@id='menuLoggedIn']")
    m1 = top_menu.find_element_by_xpath(".//li[1]")

    # Activate the dropdown. May be optional
    m1.find_element_by_xpath("./a").click()

    # Locate the correct link
    m1.find_element_by_xpath(".//a[@id='gllwg07s']").click()

    WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.ID, "documentType-button")))

    driver.execute_script('document.getElementById("documentType").style = "display: block;"')
    sel = Select(driver.find_element_by_xpath("//select[@id='documentType'] | //select[@name='documentType']"))
    sel.select_by_value('kontoutskrift')

def extract(driver, config):
    """ Extract all the statements for the accounts given """
    print("Extracting")

    file_pattern = re.compile('(\\d{11})_-_(\\d{4}-\\d{2}).*')
    for entry in config['extraction']:
        for account in entry['accounts']:
            months = list(entry['months'])
            # Wait to ensure that the correct DOM elements are loaded
            WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.ID, "documentType-button")))
            WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.ID, "accountNumber")))

            # Select the correct account
            driver.execute_script('document.getElementById("accountNumber").style = "display: block;"')
            sel = Select(driver.find_element_by_xpath("//select[@id='accountNumber'] | //select[@name='accountNumber']"))
            sel.select_by_value(account.replace('.', ''))

            # Iterate over the given months
            # Goes until all the months have been extracted, even with timeouts
            while months:
                for month in months:
                    try:
                        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.ID, "searchIntervalIndex")))
                        driver.execute_script('document.getElementById("searchIntervalIndex").style = "display: block;"')
                        sel = Select(driver.find_element_by_xpath("//select[@id='searchIntervalIndex'] | //select[@name='searchIntervalIndex']"))
                        sel.select_by_value(f"{month}")

                        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.XPATH, "//input[@id='archiveSearchSubmit']")))
                        driver.find_element_by_xpath("//input[@id='archiveSearchSubmit']").click()

                        # Wait to ensure that the correct DOM elements are loaded
                        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.XPATH, "//table//a[@href='ajax/attachment/0/kontoutskrift'] | //div[@id='userInformationView']")))
                        
                        try:
                            # Click the file to download
                            driver.find_element_by_xpath("//table//a[@href='ajax/attachment/0/kontoutskrift']").click()
                        except NoSuchElementException:
                            # Inform the user if it's not possible to download
                            print(f"Could not find financial statement for {account} in {driver.find_element_by_id('searchIntervalIndex-button').text}")
                            months.remove(month)
                    except TimeoutException:
                        print(f"Timed out for {account} on {driver.find_element_by_id('searchIntervalIndex-button').text}")
                        pass

                for file in pathlib.Path(os.getcwd()).glob('*.pdf'):
                    match = file_pattern.search(file.stem)

                    if not match:
                        continue

                    # remove reference of the file if the file has been downloaded
                    if match.group(1) == account.replace('.', '') and (month := num_months(datetime.now(), datetime.strptime(match.group(2), "%Y-%m"))) in months:
                        months.remove(month)

            combine(account)

def combine(account):
    """ Combines the downloaded pdfs into one and deletes the individual ones """

    print(f"Combining for {account}")

    # Retrieve all the files pertaining to the account
    dl_path = pathlib.Path(os.getcwd())
    files = [x for x in dl_path.glob('*.pdf') if x.stem.startswith(account.replace('.', ''))]

    merger = PdfFileMerger()

    for file in sorted(files):
        merger.append(str(file))

    # Output the merged PDF
    merger.write(f"{account}.pdf")
    merger.close()

def cleanup():
    """ A function who's whole point is to clean up files which may be missed in the combination step """

    print("Cleaning up remaining files")

    file_pattern = re.compile('(\\d{11})_-_(\\d{4}-\\d{2}).*')

    for file in pathlib.Path(os.getcwd()).glob('*.pdf'):
        match = file_pattern.search(file.stem)

        if match:
            file.unlink()

def main(argv):
    if len(argv) < 2:
        print("You need to add the path to the configuration file.")
        print("Usage: python main.py config.yaml")
        return

    config = {}
    with open(sys.argv[1], 'r') as fi:
        config |= yaml.load(fi.read(-1), Loader=Loader)

    process_config(config)

    resolve_env()

    # Instantiate the web browser and navigate to DNB
    driver = webdriver.Firefox(**configure())
    login(driver, config['ssn'])
    navigate(driver)
    extract(driver, config)
    cleanup()

    driver.quit()

if __name__ == '__main__':
    main(sys.argv)
