import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './style.css'

const github = 'https://github.com/sillohq/pawabase'
const docs = (page = 'quickstart') => `${github}/blob/main/pawabase-docs/${page}.mdx`

const featureData = {
  database: {
    label: 'Database', note: 'Typed resources', title: 'orders',
    columns: ['customer', 'status', 'total'],
    rows: [['Aster Studio', 'paid', '$240.00'], ['Northern Goods', 'pending', '$89.00'], ['Kindred Supply', 'paid', '$164.00']],
  },
  auth: {
    label: 'Authentication', note: 'Policies apply', title: 'members',
    columns: ['email', 'role', 'status'],
    rows: [['ada@acme.co', 'owner', 'active'], ['sam@acme.co', 'developer', 'active'], ['nina@acme.co', 'viewer', 'invited']],
  },
  storage: {
    label: 'Storage', note: 'Signed access', title: 'product-assets',
    columns: ['file', 'type', 'size'],
    rows: [['hero-final.png', 'image', '2.4 MB'], ['launch-film.mp4', 'video', '18.1 MB'], ['press-kit.zip', 'archive', '6.7 MB']],
  },
  realtime: {
    label: 'Realtime', note: 'Live presence', title: 'room:launch',
    columns: ['member', 'event', 'state'],
    rows: [['Ada', 'presence', 'online'], ['Sam', 'message', 'delivered'], ['Nina', 'typing', 'live']],
  },
  flows: {
    label: 'Flows', note: 'Visual logic', title: 'fulfil_order',
    columns: ['step', 'type', 'state'],
    rows: [['order.created', 'trigger', 'complete'], ['check_inventory', 'condition', 'complete'], ['reserve_stock', 'action', 'running']],
  },
  functions: {
    label: 'Functions', note: 'Run your code', title: 'price_cart',
    columns: ['invocation', 'region', 'duration'],
    rows: [['req_9214', 'local', '42 ms'], ['req_9213', 'local', '38 ms'], ['req_9212', 'local', '51 ms']],
  },
}

function Icon({ name, size = 20 }) {
  const common = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true }
  const paths = {
    database: <><ellipse cx="12" cy="5" rx="7.5" ry="3"/><path d="M4.5 5v7c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V5"/><path d="M4.5 12v7c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3v-7"/></>,
    auth: <><path d="M12 3 5 6v5c0 4.6 2.8 8.5 7 10 4.2-1.5 7-5.4 7-10V6l-7-3Z"/><path d="m9 12 2 2 4-4"/></>,
    storage: <><path d="M3.5 7.5h6l2-2h9v14h-17z"/><path d="M3.5 9.5h17"/></>,
    realtime: <><path d="M4 14a8 8 0 0 1 12.8-6.4"/><path d="M20 10a8 8 0 0 1-12.8 6.4"/><path d="m16 4 .8 3.6-3.6.8"/><path d="m8 20-.8-3.6 3.6-.8"/></>,
    flows: <><rect x="3" y="4" width="6" height="5" rx="1"/><rect x="15" y="15" width="6" height="5" rx="1"/><path d="M9 6.5h3a4 4 0 0 1 4 4V15"/><path d="m13.5 12.5 2.5 2.5 2.5-2.5"/></>,
    functions: <><path d="M8 3H5.5A2.5 2.5 0 0 0 3 5.5v13A2.5 2.5 0 0 0 5.5 21H8"/><path d="m14 8 4 4-4 4"/><path d="M18 12H8"/></>,
    arrow: <><path d="M5 12h14"/><path d="m14 7 5 5-5 5"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
    copy: <><rect x="8" y="8" width="11" height="11" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/></>,
    spark: <><path d="m12 3 1.2 4.3L17 9l-3.8 1.7L12 15l-1.2-4.3L7 9l3.8-1.7L12 3Z"/><path d="m19 15 .6 2.1 1.9.9-1.9.9L19 21l-.6-2.1-1.9-.9 1.9-.9L19 15Z"/></>,
    menu: <><path d="M4 7h16M4 12h16M4 17h16"/></>,
    close: <><path d="m6 6 12 12M18 6 6 18"/></>,
  }
  return <svg {...common}>{paths[name]}</svg>
}

function Header() {
  const [open, setOpen] = useState(false)
  return <header className="site-header">
    <a className="brand" href="#top" aria-label="Pawabase home"><span className="brand-mark"><img src="/favicon.svg" alt="" /></span><span>pawabase</span></a>
    <button className="menu-button" aria-label="Toggle navigation" aria-expanded={open} onClick={() => setOpen(!open)}><Icon name={open ? 'close' : 'menu'} /></button>
    <nav className={open ? 'main-nav is-open' : 'main-nav'} aria-label="Main navigation">
      <a href="#platform" onClick={() => setOpen(false)}>Platform</a><a href="#developers" onClick={() => setOpen(false)}>Developers</a><a href={github}>Open source</a><a href={docs()}>Docs</a>
    </nav>
    <a className="header-action" href={docs('school/going-live')}>Start building <Icon name="arrow" size={16} /></a>
  </header>
}

function CodePanel() {
  const [mode, setMode] = useState('query')
  const [copied, setCopied] = useState(false)
  const snippets = {
    query: `import { createClient } from "@pawabase/client"\n\nconst pawa = createClient({\n  url: "https://api.myapp.com",\n  apiKey: "pb_pk_••••••••",\n})\n\nconst { data, total } = await pawa\n  .from("orders")\n  .eq("status", "paid")\n  .order("created_at", "desc")`,
    auth: `const result = await pawa.auth\n  .signInWithPassword({\n    email: "ada@acme.co",\n    password: "••••••••",\n  })\n\nif (result.status === "mfa_required") {\n  await result.verify(code)\n}`,
  }
  const copy = async () => { try { await navigator.clipboard.writeText(snippets[mode]); setCopied(true) } catch { setCopied(false) } }
  useEffect(() => { if (!copied) return; const timer = setTimeout(() => setCopied(false), 1600); return () => clearTimeout(timer) }, [copied])
  return <div className="code-card glass-card">
    <div className="card-top code-top"><div className="window-dots"><i/><i/><i/></div><div className="code-tabs" role="tablist" aria-label="SDK examples"><button className={mode === 'query' ? 'active' : ''} onClick={() => setMode('query')}>Query</button><button className={mode === 'auth' ? 'active' : ''} onClick={() => setMode('auth')}>Auth</button></div><button className="copy-button" onClick={copy} aria-label="Copy code"><Icon name={copied ? 'check' : 'copy'} size={15}/></button></div>
    <pre aria-live="polite"><code>{mode === 'query' ? <><span className="pink">import</span> {'{ '}<span className="blue">createClient</span>{' } '}<span className="pink">from</span> <span className="green">"@pawabase/client"</span>{'\n\n'}<span className="pink">const</span> pawa = <span className="blue">createClient</span>{'({\n  '}url: <span className="green">"https://api.myapp.com"</span>{',\n  '}apiKey: <span className="green">"pb_pk_••••••••"</span>{',\n})\n\n'}<span className="pink">const</span> {'{ data, total } = '}<span className="pink">await</span>{' pawa\n  .'}<span className="blue">from</span><span className="gold">("orders")</span>{'\n  .'}<span className="blue">eq</span><span className="gold">("status", "paid")</span>{'\n  .'}<span className="blue">order</span><span className="gold">("created_at", "desc")</span></> : <><span className="pink">const</span>{' result = '}<span className="pink">await</span>{' pawa.auth\n  .'}<span className="blue">signInWithPassword</span>{'({\n    '}email: <span className="green">"ada@acme.co"</span>{',\n    '}password: <span className="green">"••••••••"</span>{',\n  })\n\n'}<span className="pink">if</span>{' (result.status === '}<span className="green">"mfa_required"</span>{') {\n  '}<span className="pink">await</span>{' result.'}<span className="blue">verify</span>{'(code)\n}'}</>}</code></pre>
    <div className="code-foot"><span><i/> Type-safe response</span><b>TypeScript</b></div>
  </div>
}

function StudioPreview({ active }) {
  const item = featureData[active]
  return <div className="studio-wrap"><div className="studio-card">
    <div className="studio-chrome"><div className="window-dots"><i/><i/><i/></div><span>app.pawabase.com</span><b>↗</b></div>
    <div className="studio-shell"><aside className="studio-sidebar"><div className="mini-brand"><img src="/favicon.svg" alt="" /></div>{Object.keys(featureData).map(name => <span key={name} className={name === active ? 'active' : ''}><Icon name={name} size={16}/></span>)}<span className="sidebar-avatar">A</span></aside>
      <section className="studio-content"><div className="studio-head"><div><small>cloud-kingdom / production</small><h3>{item.title}</h3></div><button>+ New record</button></div><div className="studio-controls"><span><Icon name={active} size={13}/>{item.label}</span><span>Filter</span><span>Sort</span><b>{item.rows.length} records</b></div>
        <div className="data-table" role="table" aria-label={`${item.label} preview`}><div className="table-row table-header" role="row"><span>#</span>{item.columns.map(col => <span key={col}>{col}</span>)}</div>{item.rows.map((row, index) => <div className="table-row" role="row" key={row[0]}><span>{String(index + 1).padStart(2, '0')}</span>{row.map((cell, i) => <span className={i === 1 ? 'status-cell' : ''} key={cell}>{cell}</span>)}</div>)}</div>
        <div className="studio-chart"><div><small>API REQUESTS</small><b>2,847</b></div><svg viewBox="0 0 440 86" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#9d7cff" stopOpacity=".32"/><stop offset="1" stopColor="#9d7cff" stopOpacity="0"/></linearGradient></defs><path className="area" d="M0 72 C35 69 38 50 72 58 S112 78 145 45 S195 56 226 38 S267 61 301 29 S348 47 378 24 S412 33 440 7 L440 86 L0 86Z"/><path className="line" d="M0 72 C35 69 38 50 72 58 S112 78 145 45 S195 56 226 38 S267 61 301 29 S348 47 378 24 S412 33 440 7"/></svg></div>
      </section></div></div><div className="live-toast"><span><Icon name="check" size={13}/></span><div><b>{item.label} is live</b><small>{item.note}</small></div></div></div>
}

function FeaturePicker({ active, setActive }) {
  return <div className="feature-picker glass-card"><div className="card-top"><span>Everything included</span><i>06</i></div><div className="feature-buttons">{Object.entries(featureData).map(([key, item]) => <button key={key} className={active === key ? 'active' : ''} onClick={() => setActive(key)} aria-pressed={active === key}><span><Icon name={key} size={21}/></span><b>{item.label}</b><small>{item.note}</small></button>)}</div></div>
}

function Hero() {
  const [active, setActive] = useState('database')
  return <section className="hero" id="top"><div className="hero-glow hero-glow-one"/><div className="hero-glow hero-glow-two"/><div className="hero-copy"><div className="hero-kicker"><span><Icon name="spark" size={14}/></span> The open backend platform</div><h1>Build your backend.<br/><em>Keep your momentum.</em></h1><p>Database, auth, storage, realtime and backend logic—connected in one place, with an SDK that stays out of your way.</p><div className="hero-actions"><a className="primary-button" href={docs('school/going-live')}>Start building free <Icon name="arrow" size={17}/></a><a className="secondary-button" href={github}>View on GitHub <span>↗</span></a></div></div><div className="hero-stage" aria-label="Pawabase SDK, Studio, and platform features"><CodePanel/><StudioPreview active={active}/><FeaturePicker active={active} setActive={setActive}/></div><div className="hero-note"><span>One client</span><i/><span>One workspace</span><i/><span>One connected backend</span></div></section>
}

const platformFeatures = [
  ['database', 'Data that becomes an API', 'Define a resource once. Pawabase turns it into validated endpoints, policies, docs and generated types.'],
  ['auth', 'Access that lives with your data', 'Sessions, organizations and operation-level policies stay connected to the resources they protect.'],
  ['flows', 'Logic you can see and follow', 'Move work from requests, schedules and events through clear flows with inspectable runs.'],
]

function Platform() {
  return <section className="platform-section" id="platform"><div className="section-heading"><p className="eyebrow">ONE CONNECTED SYSTEM</p><h2>Stop stitching your backend together.</h2><p>Each piece is useful on its own. The real advantage is how naturally they work together.</p></div><div className="platform-grid">{platformFeatures.map(([icon, title, copy], index) => <article key={title} className={`platform-card card-${index + 1}`}><div className="platform-icon"><Icon name={icon} size={24}/></div><span>0{index + 1}</span><h3>{title}</h3><p>{copy}</p><a href={docs(icon === 'database' ? 'data/resources' : icon === 'auth' ? 'GROUND_TRUTH_auth' : 'flows/overview')}>Explore {title.split(' ')[0].toLowerCase()} <Icon name="arrow" size={15}/></a></article>)}</div></section>
}

function DeveloperSection() {
  return <section className="developer-section" id="developers"><div className="developer-copy"><p className="eyebrow">BUILT FOR THE WAY YOU WORK</p><h2>A small SDK for a big backend.</h2><p>Use standard <code>fetch</code> and WebSocket under the hood, generate types from your environment, and handle records, auth, files, functions, flows and realtime through one client.</p><ul><li><Icon name="check" size={15}/>Zero runtime dependencies</li><li><Icon name="check" size={15}/>ESM and CommonJS, with types</li><li><Icon name="check" size={15}/>Retries, refresh and typed errors built in</li></ul><a className="primary-button dark-button" href={`${github}/tree/main/sdk/typescript`}>Read the SDK docs <Icon name="arrow" size={17}/></a></div>
    <div className="terminal-card"><div className="terminal-head"><div className="window-dots"><i/><i/><i/></div><span>quickstart.ts</span><small>TypeScript</small></div><div className="terminal-body"><p><span className="line-no">1</span><span className="pink">import</span> {'{ createClient } '}<span className="pink">from</span> <span className="green">"@pawabase/client"</span></p><p><span className="line-no">2</span></p><p><span className="line-no">3</span><span className="pink">const</span> pawa = createClient&#40;&#123; url, apiKey &#125;&#41;</p><p><span className="line-no">4</span></p><p><span className="line-no">5</span><span className="pink">const</span> posts = pawa.<span className="blue">from</span>&#40;<span className="green">"posts"</span>&#41;</p><p><span className="line-no">6</span><span className="pink">await</span> posts.<span className="blue">create</span>&#40;&#123; title: <span className="green">"Hello, world"</span> &#125;&#41;</p><p><span className="line-no">7</span><span className="pink">await</span> posts.<span className="blue">eq</span>&#40;<span className="green">"status"</span>, <span className="green">"live"</span>&#41;.<span className="blue">all</span>&#40;&#41;</p></div><div className="terminal-result"><span><Icon name="check" size={14}/> Request complete</span><b>200 OK</b><small>42 ms</small></div></div>
  </section>
}

function Closing() {
  return <><section className="closing-section"><div className="closing-orbit orbit-a"/><div className="closing-orbit orbit-b"/><div className="closing-mark"><img src="/favicon.svg" alt=""/></div><p className="eyebrow">YOUR BACKEND IS READY</p><h2>Start with one piece.<br/><em>Keep the whole system.</em></h2><p>Run Pawabase and build your first connected backend.</p><a className="primary-button light-button" href={docs('school/going-live')}>Start building <Icon name="arrow" size={17}/></a></section><footer><div className="footer-main"><div><a className="brand footer-brand" href="#top"><span className="brand-mark"><img src="/favicon.svg" alt=""/></span><span>pawabase</span></a><p>One backend. Already connected.</p></div><div><b>Platform</b><a href="#platform">Product</a><a href="#developers">Developers</a><a href={github}>Open source</a></div><div><b>Resources</b><a href={docs()}>Documentation</a><a href={`${github}/tree/main/sdk/typescript`}>TypeScript SDK</a><a href={docs('reference/changelog')}>Changelog</a></div></div><div className="footer-bottom"><span>© 2026 Pawabase</span><span>Built for builders who like momentum.</span></div></footer></>
}

function App() { return <><a className="skip-link" href="#top">Skip to content</a><Header/><main><Hero/><Platform/><DeveloperSection/><Closing/></main></> }

createRoot(document.getElementById('root')).render(<App />)
