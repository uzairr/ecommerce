import csv

from bok_choy.web_app_test import WebAppTest
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

from acceptance_tests.mixins import LogistrationMixin


class FixGradeTests(LogistrationMixin, WebAppTest):
    def test_blah(self):
        self.login()

        urls = []
        with open('/Users/cblackburn/Desktop/credit_fix.csv') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                url = 'https://courses.edx.org/courses/{course_id}/progress/{user_id}/'.format(course_id=row['course_key'], user_id=row['id'])
                urls.append(url)

        for url in urls:
            self.browser.get(url)
            wait = WebDriverWait(self.browser, 30)
            graph_present = EC.presence_of_element_located((By.ID, 'grade-detail-graph'))
            wait.until(graph_present)
