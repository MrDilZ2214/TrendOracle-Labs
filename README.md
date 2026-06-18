# TrendOracle Labs

**AI-Powered Crypto Intelligence Platform**  
Built for **Bitget AI x Crypto Trading Hackathon 2026**

🚧 Under active development | Functional MVP Ready

![Status](https://img.shields.io/badge/Status-MVP_Complete-brightgreen) 
![Python](https://img.shields.io/badge/Python-3.10%2B-blue) 
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green) 
![Bitget](https://img.shields.io/badge/Bitget-Integrated-orange)

## Overview
TrendOracle Labs is an AI-powered crypto intelligence platform designed to help traders make better decisions through market analysis, news intelligence, whale tracking, and AI-generated trading insights.

It aggregates complex market data in real-time and transforms it into clear, actionable signals for crypto traders.

## Problem Statement
Crypto traders face massive information overload from constant market movements, breaking news events, social media sentiment, on-chain whale activity, and technical indicators. Manually analyzing all these sources is extremely time-consuming and often leads to missed opportunities or poor decision making.

## Solution
TrendOracle Labs leverages advanced artificial intelligence to aggregate, analyze, and simplify complex crypto market data into clear, actionable insights. This helps traders react faster, spot opportunities earlier, and make more informed trading decisions with confidence.

## Core Features
- **AI Chat Assistant**: Natural language interaction with tool-calling LLM for market queries and analysis
- **Market Intelligence Dashboard**: Real-time prices, gainers, losers, and market overview
- **Trade Proposal Engine**: AI-generated trade ideas with Entry, Stop Loss, Take Profit, and Risk:Reward ratios
- **Whale Monitoring System**: Track large wallet movements and significant on-chain activity
- **News Intelligence**: Sentiment analysis and impact assessment from latest crypto news
- **Risk Management**: Portfolio risk evaluation and position sizing recommendations
- **Demo Trading Environment**: Paper trading simulator to test strategies safely
- **Notification Center**: Custom alerts for price movements, news, and signals
- **Bitget Exchange Integration**: Live market data, tickers, and trading capabilities
- **Market Summary Agent**: Daily/periodic AI-generated market reports
- **Chart Generation**: Technical analysis charts with multiple indicators
- **Multi-Agent System**: Specialized AI agents working together

## Technology Stack
- **Backend**: FastAPI + Python
- **Frontend**: HTML, CSS, JavaScript with WebSocket support
- **AI Layer**: NVIDIA API / Large Language Models with tool calling
- **Data Sources**: Bitget API, additional exchange APIs
- **Database**: SQLite (with option for PostgreSQL)
- **Visualization**: Matplotlib / Chart.js
- **Deployment**: Local, Replit, or cloud platforms

## Quick Start

1. **Clone the Repository**
   ```bash
   git clone https://github.com/MrDilZ2214/TrendOracle-Labs.git
   cd TrendOracle-Labs
   ```

2. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure API Keys**
   - Copy `config.example.py` to `config.py`
   - Add your `NVIDIA_API_KEY`
   - Add Bitget API keys (optional for full features)

4. **Run the Application**
   ```bash
   python main.py
   ```

5. **Access the Platform**
   Open your browser and go to `http://localhost:5000`

## Project Structure
```
├── main.py                 # Main FastAPI application
├── config.py               # Configuration and API keys
├── requirements.txt        # Python dependencies
├── agents/                 # AI agent modules
├── core/                   # Core business logic
├── tools/                  # Utility and integration tools
├── docs/                   # Full documentation
├── public/                 # Static web assets
├── templates/              # HTML templates
└── charts/                 # Generated chart images
```

## Documentation
- [TEAM.md](./TEAM.md)
- [AI Integration](./docs/AI_INTEGRATION.md)
- [Architecture](./docs/architecture.md)
- [Features](./docs/features.md)
- [Roadmap](./docs/roadmap.md)

## Development Status
**Current Stage: Functional MVP Completed**

### Completed
- Repository setup and project structure
- Team documentation
- System architecture design
- AI Chat Assistant with tool calling
- Market Intelligence Dashboard
- Bitget Exchange Integration
- Trade Proposal Engine
- Whale Monitoring System
- News Intelligence Module
- Risk Management Module
- Demo Trading Environment
- Notification Center
- Basic chart generation

### In Progress
- Binance Integration
- OKX Integration
- Advanced AI optimization
- Multi-exchange intelligence
- Frontend enhancements and UI/UX improvements
- Additional technical indicators

### Future Vision
Build a comprehensive AI-powered crypto intelligence ecosystem that delivers real-time market insights, high-quality trading opportunities, advanced risk analysis, and intelligent decision support tools for traders worldwide.

---

**Built with ❤️ for Bitget AI x Crypto Trading Hackathon 2026**
