import { url } from '../lib/site';

/** Generated rather than static, so the icon paths carry the deploy base.
 *  A static public/site.webmanifest with "/icon-192.png" 404s on a project
 *  page, because the real path is "/rite/icon-192.png". */
export function GET() {
  const manifest = {
    name: 'rite',
    short_name: 'rite',
    start_url: url('/'),
    scope: url('/'),
    icons: [
      { src: url('/icon-192.png'), sizes: '192x192', type: 'image/png', purpose: 'any' },
      { src: url('/icon-512.png'), sizes: '512x512', type: 'image/png', purpose: 'any' },
      { src: url('/maskable-512.png'), sizes: '512x512', type: 'image/png', purpose: 'maskable' },
    ],
    /* Crimson stays for installed-app chrome; the page's own theme-color meta
       follows light/dark. */
    theme_color: '#B82D22',
    background_color: '#FAF7F2',
    display: 'standalone',
  };
  return new Response(JSON.stringify(manifest, null, 2), {
    headers: { 'Content-Type': 'application/manifest+json' },
  });
}
