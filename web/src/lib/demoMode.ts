/** True on the public /demo page: the app runs against in-browser sample data, no server session. */
export const isDemo = typeof location !== 'undefined' && location.pathname === '/demo';
