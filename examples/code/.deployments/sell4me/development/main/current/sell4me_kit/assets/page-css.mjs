/**
 * The CSS for the classes one storefront page uses.
 *
 * Run by `pagecss.py` as `node --input-type=module -e <this file>` from the directory that has `node_modules` (SELL4ME_NODE_DIR), so the
 * imports below resolve there however the code was deployed.
 *
 * Reads a JSON array of class names on stdin and writes the stylesheet for
 * exactly those to stdout. Called by `app/services/pagecss.py`, which caches
 * the result by a hash of the classes, so this runs once per distinct set.
 *
 * Why it exists: the builder's Style tab writes Tailwind classes onto blocks,
 * and those classes live in the database — not in any source file the build
 * scans — so the shop's compiled stylesheet has never heard of them. Theme
 * utilities only, with theme values written inline so nothing here redefines
 * a variable the shop's own stylesheet (and its reset) already set.
 */
import { compile } from '@tailwindcss/node'

let input = ''
for await (const chunk of process.stdin) input += chunk
const classes = JSON.parse(input || '[]').filter((value) => typeof value === 'string')

const compiler = await compile(
  `@layer theme, base, components, utilities;
@import "tailwindcss/theme.css" theme(reference inline);
@import "tailwindcss/utilities.css" layer(utilities);`,
  { base: process.cwd(), onDependency() {} },
)
process.stdout.write(compiler.build(classes))
