import pytest
import pytest_asyncio
from unittest.mock import patch, MagicMock
from app.modules.profiler.scrapers.linkedin_company import read_company_page, _extract_linkedin_data, _is_login_wall

@pytest.mark.asyncio
async def test_extract_linkedin_data_public():
    # Mock HTML representing a public about page
    html = """
    <html>
        <head><title>Test Company | LinkedIn</title></head>
        <body>
            <dl>
                <dt>Website</dt><dd>http://test.com</dd>
                <dt>Industry</dt><dd>Software Development</dd>
                <dt>Company size</dt><dd>51-200 employees</dd>
                <dt>Headquarters</dt><dd>San Francisco, CA</dd>
            </dl>
            <div>1,234 followers</div>
            <h2>About</h2>
            <p>We are a software company.</p>
            <p>We build great things.</p>
        </body>
    </html>
    """
    
    data = _extract_linkedin_data(html)
    
    assert data["company_name"] == "Test Company"
    assert data["website"] == "http://test.com"
    assert data["industry"] == "Software Development"
    assert data["size"] == "51-200 employees"
    assert data["headquarters"] == "San Francisco, CA"
    assert data["followers"] == "1,234"
    assert data["about"] == "We are a software company. We build great things."

@pytest.mark.asyncio
async def test_is_login_wall():
    html_auth = "<html><title>Sign In to LinkedIn</title><body>Please login to view this page</body></html>"
    assert _is_login_wall("https://www.linkedin.com/uas/login", html_auth) == True
    
    html_public = "<html><title>Acme Corp | LinkedIn</title><body class='company-about'>Details...</body></html>"
    assert _is_login_wall("https://www.linkedin.com/company/acme/about", html_public) == False

@pytest.mark.asyncio
@patch("app.modules.profiler.scrapers.linkedin_company.async_playwright")
async def test_read_company_page_login_wall(mock_playwright):
    mock_p = MagicMock()
    mock_playwright.return_value.__aenter__.return_value = mock_p
    
    mock_browser = MagicMock()
    mock_p.chromium.launch.return_value = mock_browser
    mock_context = MagicMock()
    mock_browser.new_context.return_value = mock_context
    mock_page = MagicMock()
    mock_context.new_page.return_value = mock_page
    
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_page.goto.return_value = mock_resp
    
    mock_page.content.return_value = "<html><title>Sign In to LinkedIn</title></html>"
    mock_page.url = "https://www.linkedin.com/uas/login"
    
    result = await read_company_page("Blocked Company")
    assert result is None

