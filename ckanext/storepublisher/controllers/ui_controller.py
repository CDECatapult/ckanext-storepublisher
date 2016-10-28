# -*- coding: utf-8 -*-

# Copyright (c) 2014-2015 CoNWeT Lab., Universidad Politécnica de Madrid

# This file is part of CKAN Store Publisher Extension.

# CKAN Store Publisher Extension is free software: you can redistribute it and/or
# modify it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# CKAN Store Publisher Extension is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with CKAN Store Publisher Extension.  If not, see <http://www.gnu.org/licenses/>.

import base64
import ckan.lib.base as base
import ckan.lib.helpers as helpers
import ckan.model as model
import ckan.plugins as plugins
import logging
import os
import requests # I think that this is a new dependency but we need it

from ckanext.storepublisher.store_connector import StoreConnector, StoreException
from ckan.common import request
from pylons import config

log = logging.getLogger(__name__)

__dir__ = os.path.dirname(os.path.abspath(__file__))
filepath = os.path.join(__dir__, '../assets/logo-ckan.png')

with open(filepath, 'rb') as f:
    LOGO_CKAN_B64 = base64.b64encode(f.read())


class PublishControllerUI(base.BaseController):

    def __init__(self, name=None):
        self._store_connector = StoreConnector(config)
        self.store_url = self._store_connector.store_url

    def _sort_tags(tags):
        listOfTags = []
        # I know this could be too horrible but first I get a solution and then Ill refactor this
        tagSorted = sorted(tags, key = lambda x: x['id'])
        
        listOfTags.append(tagSorted[0])
        tagSorted.pop(0)

        # Im sorry for this double loop, ill try to optimize this
        for tag in tagSorted:
            for item in listOfTags:
                
                if tag['isRoot']:
                    listOfTags.append(tag)
                    break
                
                if tag['parentId'] == item['id']:
                    listOfTags.insert(listOfTags.index(item) + 1, tag)
                    break

        return listOfTags

    def _get_tags():
        filters = {
            'lifecycleStatus': 'Launched'
        }
        # If this doesnt work ill just make a bunch of tags manually just to test the overall functionality
        responseTags = requests.get('http://{}/catalogManagement/category'.format(self.store_url), params=filters)

        # Checking that the request finished successfully
        try:
            responseTags.raise_for_status()
        except Exception:
            log.warn('Tags couldnt be loaded')
            c.errors['Tags'] = ['Tags couldnt be loaded']
        return responseTags.json()
    
    def publish(self, id, offering_info=None, errors=None):

        c = plugins.toolkit.c
        tk = plugins.toolkit
        context = {'model': model, 'session': model.Session,
                   'user': c.user or c.author, 'auth_user_obj': c.userobj,
                   }

        # Check that the user is able to update the dataset.
        # Otherwise, he/she won't be able to publish the offering
        try:
            tk.check_access('package_update', context, {'id': id})
        except tk.NotAuthorized:
            log.warn('User %s not authorized to publish %s in the FIWARE Store' % (c.user, id))
            tk.abort(401, tk._('User %s not authorized to publish %s') % (c.user, id))

        # Get the dataset and set template variables
        # It's assumed that the user can view a package if he/she can update it
        
        # endpoint tags http://siteurl:porturl/catalogManagement/category

        dataset = tk.get_action('package_show')(context, {'id': id})
        
        listOfTags = _sort_tags(_get_tags())

        c.pkg_dict = dataset
        c.errors = {}

        # Old code, ill keep it until im sure i dont need to see the previous structure
        # Tag string is needed in order to set the list of tags in the form
        #if 'tag_string' not in c.pkg_dict:
        #    tags = [tag['name'] for tag in c.pkg_dict.get('tags', [])]
        #    c.pkg_dict['tag_string'] = ','.join(tags)

        c.pkg_dict['tag_string'] = ','.join(listOfTags['name'])

        # when the data is provided
        if request.POST:
            offering_info = {}
            offering_info['pkg_id'] = request.POST.get('pkg_id', '')
            offering_info['name'] = request.POST.get('name', '')
            offering_info['description'] = request.POST.get('description', '')
            offering_info['license_title'] = request.POST.get('license_title', '')
            offering_info['license_description'] = request.POST.get('license_description', '')
            offering_info['version'] = request.POST.get('version', '')
            offering_info['is_open'] = 'open' in request.POST

            # Get tags
            # ''.split(',') ==> ['']
            tag_string = request.POST.get('tag_string', '')
            offering_info['tags'] = [] if tag_string == '' else tag_string.split(',')

            # Read image
            # 'image_upload' == '' if the user has not set a file
            image_field = request.POST.get('image_upload', '')

            if image_field != '':
                offering_info['image_base64'] = base64.b64encode(image_field.file.read())
            else:
                offering_info['image_base64'] = LOGO_CKAN_B64

            # Convert price into float (it's given as string)
            price = request.POST.get('price', '')
            if price == '':
                offering_info['price'] = 0.0
            else:
                try:
                    offering_info['price'] = float(price)
                except Exception:
                    offering_info['price'] = price
                    log.warn('%r is not a valid price' % price)
                    c.errors['Price'] = ['"%s" is not a valid number' % price]

            # Set offering. In this way, we recover the values introduced previosly
            # and the user does not have to introduce them again
            c.offering = offering_info

            # Check that all the required fields are provided
            required_fields = ['pkg_id', 'name', 'version']
            for field in required_fields:
                if not offering_info[field]:
                    log.warn('Field %r was not provided' % field)
                    c.errors[field.capitalize()] = ['This filed is required to publish the offering']

            # Private datasets cannot be offered as open offerings
            if dataset['private'] is True and offering_info['is_open']:
                log.warn('User tried to create an open offering for a private dataset')
                c.errors['Open'] = ['Private Datasets cannot be offered as Open Offerings']

            # Public datasets cannot be offered with price
            if 'price' in offering_info and dataset['private'] is False and offering_info['price'] != 0.0 and 'Price' not in c.errors:
                log.warn('User tried to create a paid offering for a public dataset')
                c.errors['Price'] = ['You cannot set a price to a dataset that is public since everyone can access it']

            if c.errors is None:

                try:
                    offering_url = self._store_connector.create_offering(dataset, offering_info)

                    helpers.flash_success(tk._('Offering <a href="%s" target="_blank">%s</a> published correctly.' %
                                               (offering_url, offering_info['name'])), allow_html=True)

                    # FIX: When a redirection is performed, the success message is not shown
                    # response.status_int = 302
                    # response.location = '/dataset/%s' % id
                except StoreException as e:
                    c.errors['Store'] = [e.message]

        return tk.render('package/publish.html')
