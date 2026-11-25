"""Featured image extraction shared by the scraper and the API client."""
import unittest

from bs4 import BeautifulSoup

from network.featured_image import featured_image_url, image_url


class ImageUrlTests(unittest.TestCase):
    def test_lazy_attributes_win_over_placeholder(self):
        soup = BeautifulSoup(
            '<img src="data:image/gif;base64,R0lGOD" '
            'data-src="https://steamrip.com/a.jpg">',
            "lxml",
        )
        self.assertEqual(image_url(soup.img), "https://steamrip.com/a.jpg")

    def test_protocol_relative_and_relative_urls(self):
        soup = BeautifulSoup('<img data-src="//cdn.test/a.jpg">', "lxml")
        self.assertEqual(image_url(soup.img), "https://cdn.test/a.jpg")

        soup = BeautifulSoup('<img data-src="/wp-content/a.jpg">', "lxml")
        self.assertEqual(
            image_url(soup.img), "https://steamrip.com/wp-content/a.jpg"
        )


class FeaturedImageTests(unittest.TestCase):
    def test_figure_is_preferred(self):
        soup = BeautifulSoup(
            '<article><img src="/other.jpg"></article>'
            '<figure class="single-featured-image">'
            '<img data-src="/featured.jpg"></figure>',
            "lxml",
        )
        self.assertEqual(
            featured_image_url(soup), "https://steamrip.com/featured.jpg"
        )

    def test_wp_post_image_fallback(self):
        soup = BeautifulSoup('<img class="wp-post-image" src="/cover.jpg">', "lxml")
        self.assertEqual(
            featured_image_url(soup), "https://steamrip.com/cover.jpg"
        )

    def test_article_and_plain_img_fallbacks(self):
        soup = BeautifulSoup('<article><img src="/article.jpg"></article>', "lxml")
        self.assertEqual(
            featured_image_url(soup), "https://steamrip.com/article.jpg"
        )

        soup = BeautifulSoup('<img src="/plain.jpg">', "lxml")
        self.assertEqual(
            featured_image_url(soup), "https://steamrip.com/plain.jpg"
        )

    def test_missing_image(self):
        self.assertEqual(featured_image_url(BeautifulSoup("<p>none</p>", "lxml")), "")
        self.assertEqual(featured_image_url(None), "")


if __name__ == "__main__":
    unittest.main()
