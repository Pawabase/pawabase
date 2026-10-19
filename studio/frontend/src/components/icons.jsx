import {
  Activity, Add, ArchiveBox, ArrowDown2, ArrowRight2, ArrowSwapHorizontal, ArrowSwapVertical, ArrowUp2,
  Book1, Building, Calendar, Category, Chart, ChartSquare, CloseCircle, Code, Code1, Data,
  Diagram, DirectboxSend, DocumentCode2, DocumentText, Copy, Eye, Export, Flash, Folder, Global, Hierarchy3,
  Home2, Import, Key, Layer, LayoutMaximize, Link21, Lock, Login, Logout, MagicStar, Map, Maximize3,
  Maximize4, Menu, Message, MessageQuestion, Monitor, Moon, Notification, People, Play, Profile2User,
  Refresh2, RouteSquare, SearchNormal1, Send2, Setting2, ShieldTick, Status, Sun1, TableDocument,
  TickCircle, Timer1, Trash, User, Wifi,
} from "iconsax-react";

// Pawabase uses Iconsax's filled Bulk variant for shared product controls.
// Flow blocks remain custom because their marks communicate block semantics.
const BULK_ICONS = {
  overview: Home2, database: Data, resources: TableDocument, schemas: DocumentCode2,
  transformers: ArrowSwapHorizontal, policies: ShieldTick, routes: RouteSquare, gitBranch: Hierarchy3,
  explorer: Map, functions: Code, flows: Diagram, flowBlock: Diagram, subscriptions: Notification,
  schedules: Calendar, webhooks: Send2, "inbound-hooks": DirectboxSend, "mail-templates": Message,
  users: Profile2User, storage: Folder, realtime: Wifi, jobs: Layer, events: Activity,
  observability: ChartSquare, backups: ArchiveBox, releases: Layer, keys: Key, secrets: Lock,
  settings: Setting2, org: Building, team: People, mail: Message, projects: Category, audit: DocumentText,
  buckets: Folder, terminal: Monitor, panelLeft: LayoutMaximize, panelHover: LayoutMaximize,
  panelClosed: LayoutMaximize, maximize: Maximize4, minimize: Maximize3, plus: Add, x: CloseCircle,
  trash: Trash, chevronDown: ArrowDown2, chevronUp: ArrowUp2, chevronRight: ArrowRight2,
  sun: Sun1, moon: Moon, logout: Logout, external: Export, code: Code1, send: Send2, clock: Timer1,
  upload: Import, copy: Copy, check: TickCircle, menu: Menu, bolt: Flash, layers: Layer,
  play: Play, eye: Eye, braces: Code1, link: Link21, search: SearchNormal1, arrowUp: ArrowUp2,
  arrowDown: ArrowDown2, pulse: Activity, status: Status, usage: Chart, networking: Global,
  enter: Login, chevronsUpDown: ArrowSwapVertical, bell: Notification, refresh: Refresh2,
  book: Book1, help: MessageQuestion, sparkle: MagicStar,
};

// A small line-icon fallback set on a 24-unit grid. Each icon is a list of shapes:
// "c cx cy r" a circle, "r x y w h rx" a rect, "e cx cy rx ry" an ellipse,
// anything else a path.

const SHAPES = {
  overview: ["M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"],
  database: ["e 12 5 8 3", "M4 5v14c0 1.66 3.6 3 8 3s8-1.34 8-3V5", "M4 12c0 1.66 3.6 3 8 3s8-1.34 8-3"],
  resources: ["r 3 3 18 18 3", "M3 9h18M3 15h18M9 9v12"],
  columns: ["r 3 3 18 18 3", "M9 3v18"],
  schemas: ["M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5a2 2 0 0 0 2 2h1", "M16 21h1a2 2 0 0 0 2-2v-5a2 2 0 0 1 2-2 2 2 0 0 1-2-2V5a2 2 0 0 0-2-2h-1"],
  transformers: ["M16 3l4 4-4 4", "M20 7H4", "M8 21l-4-4 4-4", "M4 17h16"],
  policies: ["M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z", "m9 12 2 2 4-4"],
  routes: ["c 6 18 3", "c 18 6 3", "M6 3v12", "M18 9a9 9 0 0 1-9 9"],
  gitBranch: ["c 6 5 2", "c 18 19 2", "c 18 5 2", "M8 5h2a4 4 0 0 1 4 4v6a4 4 0 0 0 4 4", "M14 9a4 4 0 0 0 4-4"],
  explorer: ["c 12 12 9", "m16 8-2.5 5.5L8 16l2.5-5.5z", "c 12 12 1"],
  functions: ["m16 18 6-6-6-6", "m8 6-6 6 6 6", "M14 4l-4 16"],
  flows: ["r 3 3 7 7 2", "r 14 14 7 7 2", "M10 6.5h4.5a3 3 0 0 1 3 3V14"],
  flowBlock: ["r 2.5 3.5 6 6 2", "r 15.5 14.5 6 6 2", "M8.5 6.5h3a4 4 0 0 1 4 4v4", "M15.5 17.5h-3a4 4 0 0 1-4-4v-4"],
  // Flow blocks use their catalogue key as the icon name. Keeping these as
  // path-only SVGs makes them crisp at palette and node sizes without adding
  // an image dependency. Every built-in has its own silhouette; extensions
  // fall back to the generic flowBlock mark in Icon below.
  "block:trigger.http": ["c 12 12 9", "M3 12h18", "M12 3a14 14 0 0 1 0 18", "m17 8 3 4-3 4"],
  "block:trigger.event": ["M3 12h4l3-8 4 16 3-8h4", "M19 5v3", "M17.5 6.5h3"],
  "block:trigger.schedule": ["r 3 4 18 17 3", "M8 2v4M16 2v4M3 9h18", "M12 12v4l3 2"],
  "block:trigger.resource": ["e 12 6 7 3", "M5 6v10c0 1.7 3.1 3 7 3", "m16 14 2 2 4-4", "M18 16v-5"],
  "block:trigger.webhook": ["M4 4v6a4 4 0 0 0 4 4h8a4 4 0 0 1 4 4v2", "m16 17 4 4 4-4", "c 4 4 2"],
  "block:trigger.realtime": ["M5 18a8 8 0 0 1 0-12", "M8 15a4 4 0 0 1 0-6", "c 12 12 2", "m16 9 3-3 3 3"],
  "block:trigger.job": ["m12 3 8 4-8 4-8-4z", "m4 12 8 4 8-4", "m15 19 2 2 4-4"],
  "block:trigger.manual": ["m9 5 10 7-10 7z", "M5 4v16", "M3 6h4"],

  "block:control.if": ["M5 4v4a4 4 0 0 0 4 4h6", "M5 20v-4a4 4 0 0 1 4-4", "m15 8 4 4-4 4"],
  "block:control.switch": ["M4 6h7", "m8 3 3 3-3 3", "M4 18h7", "m8 15 3 3-3 3", "M14 12h6"],
  "block:control.foreach": ["m17 2 4 4-4 4", "M3 11V9a3 3 0 0 1 3-3h15", "m7 22-4-4 4-4", "M21 13v2a3 3 0 0 1-3 3H3"],
  "block:control.while": ["M20 8a8 8 0 1 0 0 8", "m20 3v5h-5", "M9 12h6"],
  "block:control.set": ["M4 7h10", "M4 12h7", "M4 17h8", "M18 11v8", "M14 15h8"],
  "block:control.merge": ["M4 4v4a4 4 0 0 0 4 4h12", "M4 20v-4a4 4 0 0 1 4-4", "m17 9 3 3-3 3"],
  "block:control.noop": ["c 12 12 9", "M8 12h8", "M12 8v.01M12 16v.01"],
  "block:control.delay": ["M6 3h12", "M6 21h12", "M8 3c0 5 3 6 4 9-1 3-4 4-4 9", "M16 3c0 5-3 6-4 9 1 3 4 4 4 9"],
  "block:control.stop": ["M8 3h8l5 5v8l-5 5H8l-5-5V8z", "M9 9l6 6M15 9l-6 6"],
  "block:control.retry": ["M20 7v5h-5", "M19 12a7 7 0 1 1-2-5", "m17 3 2 4-4 2"],
  "block:control.timeout": ["c 12 13 8", "M9 2h6", "M12 5v2", "M12 13l4-3", "M18 6l2-2"],

  "block:transform.map": ["M4 6h5", "m6 3 3 3-3 3", "M4 18h5", "m6 15 3 3-3 3", "M15 6h5M15 18h5M17.5 6v12"],
  "block:transform.template": ["M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z", "M7 8h10M7 12h6M7 16h8"],
  "block:transform.pick": ["r 3 3 18 18 3", "M8 8h8M8 12h5M8 16h3", "m16 14 2 2 3-4"],
  "block:transform.rename": ["M4 7h9M4 12h7M4 17h9", "M16 6h5v5", "M21 6l-7 7", "m17 15 4 4"],
  "block:validate.schema": ["M8 3H6a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5a2 2 0 0 0 2 2h2", "M16 3h2a2 2 0 0 1 2 2v5", "m13 16 2 2 5-6"],
  "block:json.parse": ["M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5a2 2 0 0 0 2 2h1", "M14 7h7", "m18 4 3 3-3 3"],
  "block:json.stringify": ["M16 3h1a2 2 0 0 1 2 2v5a2 2 0 0 0 2 2 2 2 0 0 0-2 2v5a2 2 0 0 1-2 2h-1", "M3 8h8M3 12h6M3 16h8"],
  "block:math.calculate": ["r 4 2 16 20 3", "M8 7h8M8 11h.01M12 11h.01M16 11h.01M8 15h.01M12 15h.01M16 15h.01M8 19h4"],
  "block:util.id": ["M4 7h16M4 17h16M9 3 7 21M17 3l-2 18", "c 19 5 2"],
  "block:crypto.hash": ["c 8 8 4", "M11 11l9 9", "M16 16l2-2M18 18l2-2", "M5 8h6"],
  "block:time.now": ["c 12 12 9", "M12 7v5l4 2", "M5 4 3 1"],
  "block:transform.filter": ["M3 5h18l-7 8v6l-4 2v-8z", "M16 5v4", "M14 7h4"],
  "block:transform.sort": ["M7 4v16", "m3 17 4 4 4-4", "M13 7h8M13 12h6M13 17h4"],
  "block:transform.group": ["r 3 4 7 7 2", "r 14 4 7 7 2", "r 8.5 15 7 6 2", "M6.5 11v2h11v-2M12 13v2"],
  "block:transform.unique": ["r 3 3 12 12 2", "r 9 9 12 12 2", "m13 14 2 2 3-4"],
  "block:transform.summarize": ["M4 5h16M4 10h10M4 15h7", "r 14 13 7 7 2", "M17.5 16v1.5H19"],
  "block:transform.batch": ["r 3 3 7 7 2", "r 14 3 7 7 2", "r 3 14 7 7 2", "r 14 14 7 7 2", "M12 8v8"],
  "block:transform.flatten": ["M5 5h6v6H5zM13 5h6v6h-6z", "M5 13h14v6H5z", "m9 8 3 3 3-3"],
  "block:transform.slice": ["c 7 7 3", "c 7 17 3", "M9.5 8.5 20 3M9.5 15.5 20 21", "M12 12h7"],

  "block:auth.require": ["r 4 10 16 11 3", "M8 10V7a4 4 0 0 1 8 0v3", "m9 15 2 2 4-4"],
  "block:auth.user": ["c 12 8 4", "M4 21a8 8 0 0 1 16 0", "M18 4v4M16 6h4"],
  "block:auth.lookup": ["c 10 9 4", "M3 21a7 7 0 0 1 14 0", "c 18 15 3", "m20 17 2 2"],
  "block:policy.check": ["M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z", "M8 12h8", "m10 9 2 3 3-5"],
  "block:resource.list": ["e 10 5 7 3", "M3 5v12c0 1.7 3.1 3 7 3s7-1.3 7-3V5", "M3 11c0 1.7 3.1 3 7 3", "M19 8v8M16 11h6"],
  "block:resource.get": ["e 10 6 7 3", "M3 6v10c0 1.7 3.1 3 7 3", "M3 12c0 1.7 3.1 3 7 3", "c 18 16 3", "m20 18 2 2"],
  "block:resource.create": ["e 10 6 7 3", "M3 6v10c0 1.7 3.1 3 7 3", "M3 12c0 1.7 3.1 3 7 3", "M19 11v8M15 15h8"],
  "block:resource.update": ["e 9 6 6 3", "M3 6v10c0 1.7 2.7 3 6 3", "M3 12c0 1.7 2.7 3 6 3", "M14 18l6-6 2 2-6 6-3 1z"],
  "block:resource.delete": ["e 10 6 7 3", "M3 6v10c0 1.7 3.1 3 7 3", "M3 12c0 1.7 3.1 3 7 3", "M16 13h6M18 11v8M21 11v8"],
  "block:db.query": ["e 9 5 6 3", "M3 5v13c0 1.7 2.7 3 6 3", "M3 11c0 1.7 2.7 3 6 3", "m14 10 6 4-6 4", "M13 14h7"],
  "block:db.transaction": ["e 9 5 6 3", "M3 5v13c0 1.7 2.7 3 6 3", "M3 11c0 1.7 2.7 3 6 3", "M14 11h7v7h-7z", "m16 14 1.5 1.5 2.5-3"],
  "block:cache.get": ["m4 6 8-4 8 4-8 4z", "M4 10l8 4 8-4M4 14l8 4 4-2", "c 19 18 2", "m20.5 19.5 1.5 1.5"],
  "block:cache.set": ["m4 5 8-4 8 4-8 4z", "M4 9l8 4 8-4M4 13l8 4 4-2", "M19 15v8M15 19h8"],
  "block:cache.delete": ["m4 5 8-4 8 4-8 4z", "M4 9l8 4 8-4M4 13l8 4 4-2", "M16 17h7"],
  "block:cache.invalidate": ["m4 5 8-4 8 4-8 4z", "M4 9l8 4 8-4M4 13l8 4 4-2", "m17 15 5 5M22 15l-5 5"],
  "block:event.emit": ["M3 13h4l3-8 4 16 3-8h4", "m17 4 2-2 2 2", "M19 2v5"],
  "block:queue.flow": ["m9 3 8 4-8 4-8-4z", "m1 12 8 4 5-2.5", "m14 18 3 3 5-6", "M17 21v-5"],
  "block:flow.call": ["r 3 3 6 6 2", "r 15 15 6 6 2", "M9 6h4a4 4 0 0 1 4 4v5", "m13 12 4 4 4-4"],
  "block:queue.function": ["m7 18-5-6 5-6", "m11 6 4 6-4 6", "M18 5v14", "m16 8 2-3 2 3"],
  "block:realtime.publish": ["c 12 12 2", "M8 16a6 6 0 0 1 0-8M16 8a6 6 0 0 1 0 8", "M5 19a10 10 0 0 1 0-14", "m18 3 4 3-4 3"],
  "block:storage.put": ["M3 7h7l2 2h9v11H3z", "M12 18v-6", "m9 15 3-3 3 3"],
  "block:storage.read": ["M3 7h7l2 2h9v11H3z", "c 12 15 3", "M12 12v6", "m18 17 3 3"],
  "block:storage.signed_url": ["M3 7h7l2 2h9v11H3z", "M8 15h4", "M15 14a3 3 0 0 1 4 4l-2 2a3 3 0 0 1-4-4"],
  "block:storage.delete": ["M3 7h7l2 2h9v11H3z", "M9 13h7M11 11v7M15 11v7"],
  "block:mail.send": ["r 2 5 20 15 2", "m2 8 10 6 10-6", "m16 3 3 2-3 2"],
  "block:http.request": ["c 10 12 7", "M3 12h14M10 5a11 11 0 0 1 0 14", "M18 9h4", "m20 7 2 2-2 2"],
  "block:webhook.send": ["M3 4v5a4 4 0 0 0 4 4h8a4 4 0 0 1 4 4v3", "m15 17 4 4 4-4", "M8 4h7", "m12 1 3 3-3 3"],
  "block:secret.get": ["r 3 10 18 11 3", "M7 10V7a5 5 0 0 1 9-3", "c 12 15 2", "M12 17v2"],
  "block:function.call": ["m7 5-5 7 5 7", "m13 5 5 7-5 7", "M10 3 8 21", "m18 3 3 3-3 3"],
  "block:log.write": ["M5 3h14v18H5z", "M8 7h8M8 11h8M8 15h5", "M16 17h3"],
  "block:metric.increment": ["M3 20h18", "M5 17v-4M10 17V8M15 17v-7M20 17V4", "m17 7 3-3 3 3"],
  "block:response.return": ["M20 4v7a4 4 0 0 1-4 4H5", "m9 10-5 5 5 5", "M14 5h6"],
  "block:error.raise": ["M12 3 2 21h20z", "M12 9v5", "M12 18v.01", "m18 5 2-2 2 2"],
  subscriptions: ["M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9", "M10.3 21a1.94 1.94 0 0 0 3.4 0"],
  schedules: ["c 12 12 9", "M12 7v5l3 2"],
  webhooks: ["M22 2 11 13", "M22 2 15 22l-4-9-9-4 20-7z"],
  "inbound-hooks": ["M22 12h-6l-2 3h-4l-2-3H2", "M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"],
  "mail-templates": ["r 2 4 20 16 3", "m22 7-10 6L2 7"],
  users: ["M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2", "c 9 7 4", "M22 21v-2a4 4 0 0 0-3-3.87", "M16 3.13a4 4 0 0 1 0 7.75"],
  storage: ["M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2z"],
  realtime: ["M4.9 19.1C1 15.2 1 8.8 4.9 4.9", "M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5", "M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5", "M19.1 4.9C23 8.8 23 15.1 19.1 19", "c 12 12 2"],
  jobs: ["m12 2 10 5-10 5L2 7z", "m2 17 10 5 10-5", "m2 12 10 5 10-5"],
  events: ["M22 12h-4l-3 9L9 3l-3 9H2"],
  observability: ["M3 3v18h18", "M18 17V9", "M13 17V5", "M8 17v-3"],
  backups: ["M12 3v12", "m7 10 5 5 5-5", "M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"],
  releases: ["m12 2 9 5-9 5-9-5z", "m3 12 9 5 9-5", "m3 17 9 5 9-5"],
  keys: ["c 7.5 15.5 5.5", "m21 2-9.6 9.6", "m15.5 7.5 3 3L22 7l-3-3"],
  secrets: ["r 3 11 18 11 3", "M7 11V7a5 5 0 0 1 10 0v4"],
  settings: ["M4 21v-7", "M4 10V3", "M12 21v-9", "M12 8V3", "M20 21v-5", "M20 12V3", "M1 14h6", "M9 8h6", "M17 16h6"],
  org: ["M3 21h18", "M5 21V7l7-4 7 4v14", "M9 9h.01M9 13h.01M9 17h.01M15 9h.01M15 13h.01M15 17h.01"],
  team: ["M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2", "c 9 7 4", "M22 21v-2a4 4 0 0 0-3-3.87", "M16 3.13a4 4 0 0 1 0 7.75"],
  mail: ["r 2 4 20 16 2", "m22 7-10 6L2 7"],
  projects: ["r 3 3 7 7 2", "r 14 3 7 7 2", "r 3 14 7 7 2", "r 14 14 7 7 2"],
  audit: ["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z", "M14 2v6h6", "M16 13H8", "M16 17H8"],
  buckets: ["M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2z"],
  terminal: ["m4 17 6-6-6-6", "M12 19h8"],
  panelLeft: ["r 3 3 18 18 3", "M9 3v18"],
  panelHover: ["r 3 3 18 18 3", "M9 3v18", "M14 9h3", "M14 13h3"],
  panelClosed: ["r 3 3 18 18 3", "M9 3v18", "m15 10-2 2 2 2"],
  maximize: ["M8 3H5a2 2 0 0 0-2 2v3", "M21 8V5a2 2 0 0 0-2-2h-3", "M3 16v3a2 2 0 0 0 2 2h3", "M16 21h3a2 2 0 0 0 2-2v-3"],
  minimize: ["M8 3v3a2 2 0 0 1-2 2H3", "M21 8h-3a2 2 0 0 1-2-2V3", "M3 16h3a2 2 0 0 1 2 2v3", "M16 21v-3a2 2 0 0 1 2-2h3"],
  plus: ["M12 5v14", "M5 12h14"],
  x: ["M18 6 6 18", "m6 6 12 12"],
  trash: ["M3 6h18", "M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6", "M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"],
  chevronDown: ["m6 9 6 6 6-6"],
  chevronUp: ["m18 15-6-6-6 6"],
  chevronRight: ["m9 18 6-6-6-6"],
  sun: ["c 12 12 4", "M12 2v2", "M12 20v2", "m4.93 4.93 1.41 1.41", "m17.66 17.66 1.41 1.41", "M2 12h2", "M20 12h2", "m6.34 17.66-1.41 1.41", "m19.07 4.93-1.41 1.41"],
  moon: ["M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z"],
  logout: ["M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4", "m16 17 5-5-5-5", "M21 12H9"],
  external: ["M15 3h6v6", "M10 14 21 3", "M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"],
  code: ["m16 18 6-6-6-6", "m8 6-6 6 6 6"],
  send: ["m22 2-7 20-4-9-9-4 20-7z", "M22 2 11 13"],
  clock: ["c 12 12 10", "M12 6v6l4 2"],
  upload: ["M12 16V4", "m7 9 5-5 5 5", "M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"],
  copy: ["r 9 9 13 13 2", "M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"],
  check: ["M20 6 9 17l-5-5"],
  menu: ["M4 6h16", "M4 12h16", "M4 18h16"],
  bolt: ["M13 2 3 14h9l-1 8 10-12h-9l1-8z"],
  layers: ["m12 2 10 5-10 5L2 7z", "m2 17 10 5 10-5", "m2 12 10 5 10-5"],
  play: ["m6 3 14 9-14 9z"],
  eye: ["M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z", "c 12 12 3"],
  braces: ["M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5a2 2 0 0 0 2 2h1", "M16 21h1a2 2 0 0 0 2-2v-5a2 2 0 0 1 2-2 2 2 0 0 1-2-2V5a2 2 0 0 0-2-2h-1"],
  link: ["M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71", "M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"],
  search: ["c 11 11 7", "m20 20-3.5-3.5"],
  arrowUp: ["M12 19V5", "m5 12 7-7 7 7"],
  arrowDown: ["M12 5v14", "m19 12-7 7-7-7"],
  pulse: ["M3 12h4l3-8 4 16 3-8h4"],
  status: ["r 3 4 18 16 2", "M3 9h18", "m8 14.5 2.5 2.5 5-5"],
  usage: ["m12 14 4-4", "M3.34 19a10 10 0 1 1 17.32 0"],
  networking: ["c 12 12 9", "M3 12h18", "M12 3a14 14 0 0 1 0 18", "M12 3a14 14 0 0 0 0 18"],
  enter: ["M20 4v7a4 4 0 0 1-4 4H4", "m9 10-5 5 5 5"],
  chevronsUpDown: ["m7 15 5 5 5-5", "m7 9 5-5 5 5"],
  bell: ["M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9", "M10.3 21a1.94 1.94 0 0 0 3.4 0"],
  refresh: ["M21 12a9 9 0 1 1-2.64-6.36", "M21 3v6h-6"],
  book: ["M4 19.5A2.5 2.5 0 0 1 6.5 17H20", "M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"],
  help: ["c 12 12 10", "M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3", "M12 17h.01"],
  sparkle: ["M12 3l1.9 5.8L20 11l-6.1 2.2L12 19l-1.9-5.8L4 11l6.1-2.2z"],
};

function shape(spec, i) {
  const [kind, ...n] = spec.split(" ");
  if (kind === "c") return <circle key={i} cx={n[0]} cy={n[1]} r={n[2]} />;
  if (kind === "r") return <rect key={i} x={n[0]} y={n[1]} width={n[2]} height={n[3]} rx={n[4] || 0} />;
  if (kind === "e") return <ellipse key={i} cx={n[0]} cy={n[1]} rx={n[2]} ry={n[3]} />;
  return <path key={i} d={spec} />;
}

export function Icon({ name, size, className = "", style }) {
  const BulkIcon = BULK_ICONS[name];
  if (BulkIcon) return <BulkIcon size={size || 18} color="currentColor" variant="Bulk" className={`icon [svg&]:w-[18px] [svg&]:h-[18px] [svg&]:shrink-0 ${className}`} style={style} aria-hidden />;
  const shapes = SHAPES[name] || (name?.startsWith("block:") ? SHAPES.flowBlock : SHAPES.layers);
  return (
    <svg
      viewBox="0 0 24 24"
      className={`icon [svg&]:w-[18px] [svg&]:h-[18px] [svg&]:shrink-0 ${className}`}
      style={size ? { width: size, height: size, ...style } : style}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      {shapes.map(shape)}
    </svg>
  );
}
