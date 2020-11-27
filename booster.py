import json
import numpy as np
import random

from constants import MTG_COLORS, SET_PATH


def card_finder(cardlist, uuid):
    for card in cardlist:
        if card['uuid'] == uuid:
            return card


class Booster():
    def __init__(self, mtg_set):
        self.set = mtg_set
        self.cards = self.generate(self.set)

    @staticmethod
    def generate(mtg_set):
        set_data = None
        with open(f'{SET_PATH}/{mtg_set.upper()}.json', encoding='UTF-8') as f:
            set_data = json.load(f)['data']
        
        all_cards = set_data['cards']
        booster_info = set_data['booster']['default']

        config_weights = list(map(lambda d: d['weight'], booster_info['boosters']))
        seeding = random.choices(booster_info['boosters'], weights=config_weights)
        seeding = seeding[0]['contents']

        all_sheets = booster_info['sheets']

        pack_list = []
        for sheet in seeding:
            sheet_info = all_sheets[sheet]
            sheet_cards = sheet_info['cards']

            color_balance = False
            if 'balanceColors' in sheet_info:
                if sheet_info['balanceColors']:
                    color_balance = True
            
            chosen_cards = []
            is_color_balanced = False
            while not is_color_balanced:
                is_color_balanced = not color_balance
                total_weight = sum(list(sheet_cards.values()))
                card_weights = list(map(lambda w: w / total_weight, list(sheet_cards.values())))
                card_weights[-1] = 1.0 - sum(card_weights[:-1])
                chosen_cards = np.random.choice(list(sheet_cards.keys()),
                                                size=seeding[sheet],
                                                replace=False,
                                                p=card_weights)
                
                if color_balance:
                    color_dist = {c:0 for c in MTG_COLORS}
                    for uuid in chosen_cards:
                        card = card_finder(all_cards, uuid)
                        card_colors = card['colors']
                        if len(card_colors) == 1:
                            color_dist[card_colors[0]] += 1

                    is_color_balanced = True
                    for v in color_dist.values():
                        if v < 1:
                            is_color_balanced = False
                        if v > 3:
                            is_color_balanced = False

            for card in chosen_cards:
                pack_list.append(card_finder(all_cards, card))

        return pack_list
    
    @staticmethod
    def cardlist_to_scryfall(cardlist, mtg_set):
        mtg_set = mtg_set.lower()
        query = f'set%3A{mtg_set}+%28'

        for card in cardlist:
            coll_no = card['number']
            query += f'cn%3A{coll_no}+or+'

        scryfall_url = f'https://scryfall.com/search?q={query}%29&as=grid'

        return scryfall_url