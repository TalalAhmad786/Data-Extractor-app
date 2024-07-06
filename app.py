from flask import Flask, render_template, request, redirect, url_for, jsonify, Response
from flask import send_from_directory
import threading
import os
import queue
import time
from selenium import webdriver
import datetime
from selenium.webdriver.chrome.options import Options
import pandas as pd
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException

app = Flask(__name__)
update_queue = queue.Queue()
@app.route('/updates')
def updates():
    def event_stream():
        while True:
            try:
                data = update_queue.get(timeout=1)
                yield f"data: {data}\n\n"
            except queue.Empty:
                continue

    return Response(event_stream(), mimetype="text/event-stream")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/scrape', methods=['POST'])
def scrape():
    start_mc_mx_number = request.form['start_mc_mx_number']
    end_mc_mx_number = request.form.get('end_mc_mx_number')

    # CHROMEDRIVER_PATH = 'https://storage.googleapis.com/chrome-for-testing-public/126.0.6478.126/win64/chromedriver-win64.zip'

    chrome_options = Options()
    # chrome_options.add_argument('--headless')
    chrome_options.binary_location = os.environ.get("GOOGLE_CHROME_BIN")
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_experimental_option('excludeSwitches', ['enable-automation'])
    chrome_options.add_experimental_option('useAutomationExtension', False)

    driver = webdriver.Chrome(executable_path=os.environ.get("CHROMEDRIVER_PATH"), options=chrome_options)
    all_data = []
    counter = 0
    total_extracted = 0

    try:
        driver.get("https://safer.fmcsa.dot.gov/CompanySnapshot.aspx")
        mc_number_radio_button = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, '//input[@value="MC_MX"]')))
        mc_number_radio_button.click()

        batch_size = 10

        for batch_start in range(int(start_mc_mx_number), int(end_mc_mx_number) + 1, batch_size):
            batch_end = min(batch_start + batch_size - 1, int(end_mc_mx_number))
            for mc_mx_number in range(batch_start, batch_end + 1):
                while True:
                    try:
                        input_field = driver.find_element(By.ID, '4')
                        input_field.clear()
                        input_field.send_keys(str(mc_mx_number))
                        search_button = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//input[@type="SUBMIT"]')))
                        search_button.click()

                        record_not_found_message = driver.find_elements(By.XPATH, '//p/font/b/i[text()="Record Not Found"]')
                        record_inactive = driver.find_elements(By.XPATH, '//p/font/b/i[text()="Record Inactive"]')

                        if record_not_found_message or record_inactive:
                            break

                        entity_type = driver.find_element(By.XPATH, "//th[a[contains(text(),'Entity Type')]]/following-sibling::td").text.strip()
                        operating_status = driver.find_element(By.XPATH, "//th[a[contains(text(),'Operating Authority Status')]]/following-sibling::td").text
                        if entity_type == 'CARRIER' and "AUTHORIZED FOR Property" in operating_status:
                            legal_name = driver.find_element(By.XPATH, "//th[a[contains(text(),'Legal Name')]]/following-sibling::td").text
                            physical_address = driver.find_element(By.ID, "physicaladdressvalue").text.replace('\n', ' ')
                            mailing_address = driver.find_element(By.ID, "mailingaddressvalue").text.replace('\n', ' ')
                            mc_mx_ff_numbers = driver.find_element(By.XPATH, "//th[a[contains(text(),'MC/MX/FF Number(s)')]]/following-sibling::td/a").text
                            mcs_150_form_date = driver.find_element(By.XPATH, "//th[a[contains(text(),'MCS-150 Form Date')]]/following-sibling::td").text
                            phone_number = driver.find_element(By.XPATH, "//th[a[contains(text(),'Phone')]]/following-sibling::td").text

                            sms_results_link = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.LINK_TEXT, "SMS Results")))
                            sms_results_link.click()
                            
                            try:
                                carrier_registration_link = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.LINK_TEXT, "Carrier Registration Details")))
                                carrier_registration_link.click()
                            except TimeoutException:
                                pass

                            try:
                                email_element = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, "//label[contains(text(),'Email:')]/following-sibling::span[@class='dat']")))
                                email_add = email_element.text
                            except TimeoutException:
                                email_add = None

                            counter += 1
                            total_extracted += 1
                            data = {
                                'MC/MX Number': mc_mx_number,
                                'Entity Type': entity_type,
                                'Operating Status': operating_status,
                                'Legal Name': legal_name,
                                'Physical Address': physical_address,
                                'Mailing Address': mailing_address,
                                'MC/MX/FF Number(s)': mc_mx_ff_numbers,
                                'MCS-150 Form Date': mcs_150_form_date,
                                'Phone Number': phone_number,
                                'Email': email_add,
                                'Comments': None,
                                'Total Extracted': total_extracted  # Include total extracted count
                            }

                            all_data.append(data)
                            update_queue.put(total_extracted)
                            break
                        else:
                            break
                    except NoSuchElementException:
                        driver.back()
                        mc_number_radio_button = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, '//input[@value="MC_MX"]')))
                        mc_number_radio_button.click()
                        continue
    finally:
        driver.quit()

        df = pd.DataFrame(all_data)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f'company_snapshot_data_{start_mc_mx_number}_{timestamp}.csv'
        df.to_csv(filename, index=False)
        return jsonify({
            'redirect_url': url_for('download_file', filename=filename),
            'total_extracted': total_extracted  # Include total extracted count in response
        })

@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(directory='.', path=filename, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
