---
# Feel free to add content and custom Front Matter to this file.
# To modify the layout, see https://jekyllrb.com/docs/themes/#overriding-theme-defaults

layout: home
---
{% include bokeh_heading.html %}
{% include country-map.html %}
<img class="heatmap-static" src="{{site.baseurl}}/assets/img/heatmap-static.png">
<div class="hidden-on-desktop country-list"> 
<p style="font-size:18px; font-weight:bold;color:gray; text-align:center">Click on one of the nations listed below to learn more about it.</p>
{% for nation in site.data.countries %}
<a href="{{site.baseurl}}/countries/{{nation.country}}" target="_blank">{{nation.country}}</a>

{% endfor %}

</div>