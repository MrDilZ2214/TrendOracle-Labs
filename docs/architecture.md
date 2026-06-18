
# TrendOracle Labs - System Architecture

## Overview
TrendOracle Labs is an AI-powered crypto market intelligence platform designed to help traders make informed decisions through real-time market analysis, whale tracking, news intelligence, risk management, and AI-generated trade proposals.

The platform combines multiple data sources and intelligent analysis modules into a unified, user-friendly dashboard experience. It follows a modular, scalable architecture with clear separation of concerns.

## High-Level System Flow
```
User → Dashboard (Frontend) → FastAPI Backend → AI Intelligence Engine
                           ↓
                  Market Data + Whale Data + News
                           ↓
                  Trade Proposals, Insights & Alerts
```

## Core Components

### 1. Frontend Layer
- Modern, responsive web interface (HTML + CSS + JavaScript)
- Real-time updates via WebSocket
- Key Sections:
  - Live Market Dashboard (prices, gainers, losers)
  - AI Chat Assistant
  - Trade Proposals Panel
  - Whale Activity Monitor
  - News & Sentiment Feed
  - Notification Center
  - Demo Trading Portfolio
  - Settings & Configuration

### 2. Backend Services (FastAPI)
- RESTful API endpoints
- WebSocket support for real-time data
- Data processing and caching layer
- Exchange API integrations
- Authentication & session management
- Alert distribution service
- Static file serving (charts, assets)

### 3. AI Intelligence Engine (Multi-Agent System)
Powered by NVIDIA API (LLM with tool-calling):

#### Market Intelligence Engine
- Trend detection
- Price action analysis
- Volume and momentum analysis
- Volatility monitoring
- Technical indicators processing

#### Trade Proposal Engine
- Opportunity detection (Buy/Sell)
- Entry zone calculation
- Target prices (multiple)
- Stop Loss recommendation
- Risk:Reward ratio
- Confidence scoring

#### Whale Monitoring System
- Large wallet tracking
- Exchange inflow/outflow monitoring
- Unusual transaction detection
- Market impact evaluation

#### News Intelligence Module
- Real-time news collection
- Sentiment analysis (Positive/Neutral/Negative)
- Event impact assessment
- Correlation with price movements

#### Risk Management Engine
- Position sizing control
- Risk filtering
- Portfolio exposure monitoring
- User-defined risk tolerance enforcement
- Confidence threshold validation

### 4. Data Sources & Integrations
- **Primary Exchange**: Bitget (live prices, tickers)
- **Planned**: Binance, OKX
- Market data feeds
- Crypto news providers
- Blockchain/whale activity sources
- Internal SQLite database for caching and history

### 5. Notification Architecture
Generates intelligent alerts for:
- New trade opportunities
- Significant whale movements
- Major price movements
- Market summary reports

### 6. Security Layer
- API key encryption
- Local credential protection
- Secure configuration storage
- Read-only exchange permissions where possible
- Isolated demo trading environment
- No automatic real-money execution

## Project Structure
```
├── main.py                 # FastAPI entry point
├── config.py               # API keys & settings
├── agents/                 # AI agent modules
├── core/                   # Business logic & services
├── tools/                  # Exchange, data, utility tools
├── public/                 # Static assets
├── templates/              # HTML templates
├── charts/                 # Generated visualization
└── docs/                   # Documentation
```

## Technology Stack
- **Backend**: Python + FastAPI
- **AI**: NVIDIA LLM with tool calling
- **Frontend**: HTML/CSS/JS + WebSocket
- **Data**: Pandas, SQLite, Bitget API
- **Visualization**: Matplotlib / Chart.js

## Development Status
**MVP Completed** with full AI integration, Bitget connectivity, dashboard, and core engines.

## Future Expansion
- Multi-exchange support
- Advanced portfolio analytics
- Machine learning prediction models
- Personalized trading strategies
- Real-time anomaly detection
- On-chain data integration
- Mobile responsiveness improvements

---

**Part of TrendOracle Labs**  
Built for **Bitget AI x Crypto Trading Hackathon 2026**
