import type { Metadata, Viewport } from 'next';
import './globals.css';
import { ThemeProvider } from '@/components/providers/theme-provider';
import { QueryProvider } from '@/components/providers/query-provider';
import { ToastProvider } from '@/components/providers/toast-provider';
import { NavigationProgress } from '@/components/site/navigation-progress';
import { cn } from '@/lib/utils';
import { getActiveTheme } from '@/app/actions/theme-actions';
import { getActiveFont } from '@/app/actions/font-actions';
import { generateThemeCSS } from '@/config/themes';
import { displayFont, getFontById } from '@/config/fonts';
import { siteConfig } from '@/config/site';

/**
 * Metadata Configuration
 * SEO and social media optimization
 */
export const metadata: Metadata = {
  metadataBase: new URL(siteConfig.url),
  title: {
    default: siteConfig.name,
    template: `%s | ${siteConfig.name}`,
  },
  description: siteConfig.description,
  keywords: siteConfig.keywords,
  authors: [
    {
      name: siteConfig.name,
      url: siteConfig.url,
    },
  ],
  creator: siteConfig.name,
  openGraph: {
    type: 'website',
    locale: 'en_GB',
    url: siteConfig.url,
    title: siteConfig.name,
    description: siteConfig.description,
    siteName: siteConfig.name,
    images: [
      {
        url: siteConfig.ogImage,
        width: 1200,
        height: 630,
        alt: siteConfig.name,
      },
    ],
  },
  twitter: {
    card: 'summary_large_image',
    title: siteConfig.name,
    description: siteConfig.description,
    images: [siteConfig.ogImage],
  },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      'max-video-preview': -1,
      'max-image-preview': 'large',
      'max-snippet': -1,
    },
  },
  icons: {
    icon: [
      { url: '/favicon.ico' },
      { url: '/favicon-16x16.png', sizes: '16x16', type: 'image/png' },
      { url: '/favicon-32x32.png', sizes: '32x32', type: 'image/png' },
    ],
    apple: [{ url: '/apple-touch-icon.png' }],
  },
};

/**
 * Viewport Configuration
 * Responsive design and PWA settings
 */
export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 5,
  userScalable: true,
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#faf8f5' },
    { media: '(prefers-color-scheme: dark)', color: '#0b1120' },
  ],
};

/**
 * Root Layout Component
 *
 * Main layout wrapper for the entire application
 * Includes all providers and global configurations
 *
 * Features:
 * - Theme provider for dark/light mode
 * - React Query provider for data fetching
 * - Toast provider for notifications
 * - Global navigation indicator (top bar and spinner while a page loads)
 * - Font optimization with next/font
 * - Proper HTML semantics and accessibility
 *
 * @param children - Page content
 */
export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const [activeTheme, activeFontId] = await Promise.all([getActiveTheme(), getActiveFont()]);

  const { font: activeFont } = getFontById(activeFontId);

  return (
    <html lang="en" className={displayFont.variable} suppressHydrationWarning>
      <head>
        <style dangerouslySetInnerHTML={{ __html: generateThemeCSS(activeTheme) }} />
      </head>
      <body
        className={cn(activeFont.variable, 'min-h-screen bg-background font-sans antialiased')}
        suppressHydrationWarning
      >
        <ThemeProvider
          attribute="class"
          defaultTheme="system"
          enableSystem
          disableTransitionOnChange={false}
          appTheme={activeTheme}
        >
          <QueryProvider>
            <ToastProvider>
              <NavigationProgress />
              <div className="relative flex min-h-screen flex-col">
                <div className="flex-1">{children}</div>
              </div>
            </ToastProvider>
          </QueryProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
