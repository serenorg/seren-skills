---
name: browser-automation
description: "Automate web browsers using Playwright with stealth features for screenshots, web scraping, form filling, and browser interactions"
---

# Browser Automation with Playwright

Automate web browsers to perform tasks like taking screenshots, filling forms, clicking elements, and extracting data from websites.

## When to Use This Skill

Activate this skill when the user asks to:
- "Take a screenshot of a website"
- "Navigate to a URL and click a button"
- "Fill out a form on a website"
- "Scrape data from a webpage"
- "Test a website's functionality"
- "Automate browser interactions"
- "Extract text/images from a site"
- "Monitor a website for changes"

## Available Playwright Tools

The Playwright MCP server provides these tools (auto-available when this skill is active):

### Navigation
- **playwright_navigate** - Navigate to a URL
  - Args: `url` (string) - The URL to navigate to
  - Example: Navigate to https://example.com

- **playwright_navigate_back** - Go back in browser history
- **playwright_navigate_forward** - Go forward in browser history

### Interactions
- **playwright_click** - Click an element on the page
  - Args: `selector` (string) - CSS selector for the element
  - Example: Click button with selector "button.submit"

- **playwright_fill** - Fill a form input field
  - Args: `selector` (string), `value` (string)
  - Example: Fill email input: selector="#email", value="user@example.com"

- **playwright_select** - Select an option from a dropdown
  - Args: `selector` (string), `value` (string)
  - Example: Select country: selector="#country", value="US"

- **playwright_hover** - Hover over an element
  - Args: `selector` (string)
  - Example: Hover over menu item

- **playwright_press** - Press a keyboard key
  - Args: `selector` (string), `key` (string)
  - Example: Press Enter in search box: selector="#search", key="Enter"

### Content Extraction
- **playwright_screenshot** - Capture a screenshot
  - Args: `name` (string, optional) - Filename for screenshot
  - Returns: Base64-encoded image or file path
  - Example: Screenshot current page

- **playwright_evaluate** - Execute JavaScript in the browser
  - Args: `script` (string) - JavaScript code to execute
  - Returns: Result of script execution
  - Example: Extract page title: script="document.title"

- **playwright_extract_content** - Extract text content from page
  - Args: `selector` (string, optional) - Limit to specific element
  - Returns: Text content
  - Example: Extract all paragraph text

## For Claude: How to Use This Skill

When invoked, use the Playwright MCP tools to fulfill the user's browser automation request.

### Example: Take a Screenshot

```
User: "Take a screenshot of example.com"

Steps:
1. Use playwright_navigate: { url: "https://example.com" }
2. Wait for page load (automatic)
3. Use playwright_screenshot: { name: "example-screenshot.png" }
4. Return screenshot to user
```

### Example: Fill and Submit a Form

```
User: "Go to contact-form.com and fill out the contact form with my email"

Steps:
1. Use playwright_navigate: { url: "https://contact-form.com" }
2. Use playwright_fill: { selector: "#email", value: "user@email.com" }
3. Use playwright_fill: { selector: "#message", value: "Hello!" }
4. Use playwright_click: { selector: "button[type=submit]" }
5. Confirm submission success
```

### Example: Web Scraping

```
User: "Extract all product prices from shop.com/products"

Steps:
1. Use playwright_navigate: { url: "https://shop.com/products" }
2. Use playwright_evaluate with script:
   ```javascript
   Array.from(document.querySelectorAll('.product-price'))
     .map(el => el.textContent.trim())
   ```
3. Return list of prices
```

### Example: Multi-Step Workflow

```
User: "Search for 'playwright tutorial' on Google and screenshot the results"

Steps:
1. Use playwright_navigate: { url: "https://google.com" }
2. Use playwright_fill: { selector: "input[name=q]", value: "playwright tutorial" }
3. Use playwright_press: { selector: "input[name=q]", key: "Enter" }
4. Wait for results to load (automatic)
5. Use playwright_screenshot: { name: "google-results.png" }
6. Return screenshot
```

## CSS Selectors Guide

To interact with web elements, you need CSS selectors. Common patterns:

- **By ID**: `#element-id` → `<div id="element-id">`
- **By Class**: `.class-name` → `<div class="class-name">`
- **By Tag**: `button` → `<button>`
- **By Attribute**: `[name="email"]` → `<input name="email">`
- **By Text**: `text=Submit` → Element containing "Submit"
- **Nested**: `form button.submit` → Submit button inside a form
- **Nth Child**: `li:nth-child(2)` → Second list item

## Best Practices

### 1. Always Navigate First
Before interacting with elements, navigate to the page:
```
playwright_navigate → wait → interact with elements
```

### 2. Use Specific Selectors
Prefer unique selectors (IDs) over generic ones:
- ✅ Good: `#login-button`
- ❌ Bad: `button` (might match multiple buttons)

### 3. Handle Dynamic Content
For pages with JavaScript rendering:
1. Navigate to page
2. Execute JavaScript to check if content loaded
3. Then interact with elements

### 4. Screenshots for Debugging
If automation fails, take a screenshot to see current page state

### 5. Respect robots.txt and Terms of Service
- Only scrape public data
- Check website's robots.txt
- Avoid overwhelming servers with requests
- Respect rate limits

## Common Use Cases

### 1. Website Monitoring
```
Check if a website changed:
- Navigate to site
- Extract content
- Compare to previous version
- Alert if different
```

### 2. Data Collection
```
Gather information from multiple pages:
- Navigate to listing page
- Extract item links
- For each link: navigate → extract data
- Compile results
```

### 3. Automated Testing
```
Test user workflows:
- Navigate to app
- Fill login form
- Click login button
- Verify dashboard loads
- Screenshot success state
```

### 4. Form Submission
```
Auto-fill repetitive forms:
- Navigate to form page
- Fill all fields
- Submit form
- Verify confirmation message
```

### 5. Content Archiving
```
Archive web pages:
- Navigate to page
- Take full-page screenshot
- Extract all text content
- Save for offline access
```

## Limitations

- **Same-origin only**: Cannot interact with cross-origin iframes
- **Authentication**: May require manual login for protected pages
- **CAPTCHAs**: Cannot solve CAPTCHAs (requires human interaction)
- **Dynamic sites**: Complex SPAs may need custom wait logic
- **Rate limiting**: Some sites may block automated access

## Troubleshooting

### Element Not Found
- Verify selector is correct (use browser DevTools)
- Check if page fully loaded
- Try more specific selector
- Screenshot page to see current state

### Navigation Failed
- Check URL is valid and accessible
- Verify network connection
- Try with different browser headers

### Script Execution Failed
- Verify JavaScript syntax
- Check browser console for errors
- Ensure script doesn't reference undefined variables

## Examples in Chat

Here are examples of what users can ask:

✅ **Simple Tasks:**
- "Screenshot the homepage of example.com"
- "What's the title of the page at mysite.com?"
- "Click the 'Sign Up' button on app.com"

✅ **Data Extraction:**
- "Get all the prices from this product page: [URL]"
- "Extract the main headline from news.com"
- "List all links on the page"

✅ **Workflows:**
- "Search for 'climate change' on Wikipedia and screenshot the article"
- "Go to form.com, fill in the contact form with my info, and submit"
- "Navigate to dashboard.app.com and click on the Settings tab"

✅ **Monitoring:**
- "Check if the price on this product page changed: [URL]"
- "Screenshot the current state of status.service.com"
- "Extract the latest blog post title from blog.com"

## Security & Privacy

- Playwright runs locally on your machine
- No browsing data is sent to external services
- Browser sessions are isolated (no cookies/storage persistence by default)
- Screenshots may contain sensitive information - review before sharing
- Automated form submissions should only be used on sites you own/control

## Learn More

- Playwright Documentation: https://playwright.dev
- Playwright MCP Server: https://github.com/microsoft/playwright-mcp
- CSS Selectors: https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_Selectors

---

**Ready to automate?** Tell me what website task you want to accomplish and I'll help you build the automation!
