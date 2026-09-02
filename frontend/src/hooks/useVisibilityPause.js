import { useEffect, useState } from 'react';

/**
 * Hook that returns true when the document is visible.
 *
 * Polling intervals should pause when the tab is hidden to avoid:
 * - Wasting bandwidth on data nobody is looking at
 * - Causing UI lag from re-renders on a hidden page
 * - Hitting rate limits unnecessarily
 *
 * Usage:
 *   const isVisible = useVisibilityPause();
 *   useEffect(() => {
 *     if (!isVisible) return;
 *     const id = setInterval(fetchData, 5000);
 *     return () => clearInterval(id);
 *   }, [isVisible, fetchData]);
 */
export function useVisibilityPause() {
  const [isVisible, setIsVisible] = useState(true);

  useEffect(() => {
    const handleVisibilityChange = () => {
      setIsVisible(document.visibilityState === 'visible');
    };

    // Set initial state
    setIsVisible(document.visibilityState === 'visible');

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange);
  }, []);

  return isVisible;
}
