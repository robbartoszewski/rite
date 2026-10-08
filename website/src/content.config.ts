import { defineCollection } from 'astro:content';
import { z } from 'astro:schema';
import { glob } from 'astro/loaders';

/** Blog collection. The required source list is handover §6.3 ("every proof
 *  point links to source") applied to posts: the build fails on a post that is
 *  missing a source, so none can ship unsourced. */
const blog = defineCollection({
  loader: glob({ pattern: '**/*.{md,mdx}', base: './src/content/blog' }),
  schema: z
    .object({
      title: z.string().max(90),
      /** The dek. Also the meta description, so keep it under 200. */
      description: z.string().max(200),
      /** Required once draft is false — see the refine below. */
      date: z.coerce.date().optional(),
      category: z.enum(['changelog-note', 'design-decision', 'measurement']),
      author: z.string(),
      appliesTo: z.string().regex(/^v\d+\.\d+\.\d+/),
      draft: z.boolean().default(true),
      /** At least one source, always. A post with no source does not build. */
      sources: z.array(z.object({ label: z.string(), href: z.string() })).min(1),
    })
    .refine((p) => p.draft || p.date, { message: 'Published posts need a date' }),
});

export const collections = { blog };
