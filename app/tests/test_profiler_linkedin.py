import pytest
import pytest_asyncio
from unittest.mock import patch, MagicMock
from app.modules.profiler.scrapers.linkedin_company import scrape_linkedin_company, _extract_linkedin_data, _is_login_wall

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
            <div class="update-components-text">We just launched a new product!</div>
            <div class="update-components-text">Hiring software engineers!</div>
        </body>
    </html>
    """
    
    data = _extract_linkedin_data(html)
    
    assert data["company_name"] == "Test Company"
    assert data["website"] == "http://test.com"
    assert data["industry"] == "Software Development"
    assert data["size"] == "51-200 employees"
    assert data["headquarters"] == "San Francisco, CA"
    assert len(data["recent_posts"]) == 2
    assert "new product" in data["recent_posts"][0]

@pytest.mark.asyncio
async def test_is_login_wall():
    html_auth = "<html><title>Sign In to LinkedIn</title><body>Please login to view this page</body></html>"
    assert _is_login_wall("https://www.linkedin.com/uas/login", html_auth) == True
    
    html_public = "<html><title>Acme Corp | LinkedIn</title><body class='company-about'>Details...</body></html>"
    assert _is_login_wall("https://www.linkedin.com/company/acme/about", html_public) == False

@pytest.mark.asyncio
@patch("app.modules.profiler.scrapers.linkedin_company.StealthyFetcher")
async def test_scrape_linkedin_company_login_wall(mock_fetcher_cls):
    mock_fetcher = MagicMock()
    mock_fetcher_cls.return_value = mock_fetcher
    
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.url = "https://www.linkedin.com/uas/login"
    mock_resp.text = "<html><title>Sign In to LinkedIn</title></html>"
    
    # fetch is called via asyncio.to_thread, so it returns sync
    mock_fetcher.fetch.return_value = mock_resp
    
    result = await scrape_linkedin_company("Blocked Company", "blocked.com")
    assert result is None
