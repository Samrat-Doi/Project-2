# LLM Analysis Quiz – Automated Solver  
FastAPI + Playwright + Python 3.11

This repository contains my implementation for the **LLM Analysis Quiz** project.  
The system exposes a secure `/quiz` API endpoint which receives quiz tasks, loads the quiz page (including JavaScript execution), extracts the required data, solves the question, and submits the answer to the provided submission URL — all within the required 3-minute time window.

The project is fully containerized using **Docker** for deployment on **Hugging Face Spaces**, where the quiz servers can call the endpoint over HTTPS.

---

## 🚀 Features

### ✔ Secure Endpoint  
- Validates request JSON  
- Validates the `secret` provided in the POST request  
- Returns `403` for incorrect secrets  
- Returns `400` for malformed JSON  

### ✔ Automated Quiz Solver  
- Fetches and renders JavaScript-based quiz pages using **Playwright (Chromium)**  
- Extracts visible HTML contents, links, and embedded resources  
- Downloads additional files (CSV, PDF, JSON, images, etc.) if referenced  
- Supports processing tasks such as:  
  - Table extraction  
  - Text cleaning  
  - Numerical calculations  
  - Data aggregation via Pandas  
  - PDF table extraction (via `pdfplumber`)  
  - Excel file parsing (`openpyxl`)  
  - Geospatial, statistical, or custom logic based on quiz instructions  

### ✔ Automatic Answer Submission  
- Reads the “submit” URL from the quiz page  
- Sends the final answer JSON in the required structure  
- Handles multiple quiz hops (quiz → next quiz → next quiz…) until no further URL is issued  
- Ensures all processing completes before the 3-minute deadline  

### ✔ Deployed to Hugging Face Spaces  
- Implemented using a **Docker Space**, ensuring:  
  - Playwright browser installation  
  - Controlled Python version  
  - Consistent environment  
- Exposed via a public HTTPS endpoint for evaluation

---

## 📁 Project Structure

