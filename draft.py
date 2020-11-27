import asyncio
import discord
import random
import string

from discord.ext import commands

from booster import Booster


class DraftManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.drafts = {}

    @commands.command()
    async def create_draft(self, ctx, mtg_set):
        def _id_collision(draft_id):
            for draft in self.drafts.values():
                if draft['id'] == draft_id:
                    return True
            return False
        
        def _msg_check(reaction, user):
            return (reaction.message.id == self.signup_id and
                    reaction.emoji == '✋')
        
        mtg_set = mtg_set.upper()
        if mtg_set != 'CMR':
            await ctx.send('Unsupported draft format.')
            return
        
        draft_id = ''.join(random.choices(string.ascii_lowercase +
                                          string.digits, k=4))
        while _id_collision(draft_id):
            draft_id = ''.join(random.choices(string.ascii_lowercase +
                                              string.digits, k=4))

        signups = await ctx.send(f'{ctx.author.display_name} has started a {mtg_set} draft! '
                                  'React this message with ✋ to join. '
                                 f'Start early with ;start_draft {draft_id}, '
                                  'or it will fire upon reaching 8 drafters.')

        self.drafts[signups.id] = {'set': mtg_set,
                                   'players': [],
                                   'id': draft_id,
                                   'owner': ctx.author.id,
                                   'full': False,
                                   'start': asyncio.Event()}

        curr_draft = self.drafts[signups.id]
        await curr_draft['start'].wait()

        await self.run_draft(curr_draft)

    async def run_draft(self, curr_draft):

        boosters = []
        for _ in range(3 * len(curr_draft['players'])):
            boosters.append(Booster(mtg_set))

        mtg_set = curr_draft['set']
        for player in curr_draft['players']:
            await player.send(f'Starting {mtg_set} draft. '
                               'Your first pack will be given shortly.')

        self.bot.add_cog(Draft(self.bot, curr_draft['set'], curr_draft['players'],
                               boosters, curr_draft['id']))

    @commands.command()
    async def start_draft(self, ctx, draft_id):
        for draft in self.drafts.values():
            if ctx.author.id == draft['owner'] and draft_id == draft['id']:
                draft['full'] = True
                draft['start'].set()
                break

    @commands.Cog.listener('on_reaction_add')
    async def add_drafter(self, reaction, user):
        if (reaction.message.id in self.drafts and reaction.emoji == '✋'):
            draft = self.drafts[reaction.message.id]
            if not draft['full']:
                draft['players'].append(user)

                if len(draft['players']) >= 8:
                    draft['full'] = True
                    draft['start'].set()

    @commands.Cog.listener('on_raw_reaction_remove')
    async def remove_drafter(self, payload):
        if (payload.message_id in self.drafts and str(payload.emoji) == '✋'):
            player_list = self.drafts[payload.message_id]['players']
            for player in player_list:
                if payload.user_id == player.id:
                    player_list.remove(player)
                    break


class Draft(commands.Cog):
    def __init__(self, bot, mtg_set, players, boosters, draft_id):
        self.bot = bot
        self.mtg_set = mtg_set
        self.boosters = boosters
        self.id = draft_id

        random.shuffle(players)
        self.players = {}
        for player in players:
            self.players[player.id] = DraftPlayer(player, self.mtg_set)

        for i, player in enumerate(players):
            left_id = players[(i-1)%len(players)].id
            right_id = players[(i+1)%len(players)].id
            self.players[player.id].set_neighbors(self.players[left_id],
                                                  self.players[right_id])

        for player in players:
            asyncio.create_task(self.players[player.id].pack_runner(),
                                name=f'{self.id}_{player.id}_pack_q')
            await self.players[player.id].pack_q.put(self.boosters.pop())

    @commands.command()
    async def reserve(self, ctx, card_no):
        if (isinstance(ctx.channel, discord.channelDMChannel) and
                ctx.author.id in self.players):

            player_info = self.players[ctx.author.id]
            
            if not isinstance(card_no, int):
                print(type(card_no))
                print(card_no)
                return
            if card_no > len(player_info.curr_pack.cards) or card_no < 1:
                await ctx.send('Invalid pick!')
                return

            card_names = await player_info.reserve(card_no)
            card_names = '; '.join(card_names)

            if player_info.reserve_msg:
                await player_info.reserve_msg.delete()
            msg = await ctx.send(f'Currently reserved: {card_names}')
            player_info.reserve_msg = msg

    @commands.command()
    async def pick(self, ctx, card_no):
        if (isinstance(ctx.channel, discord.channel.DMChannel) and
                ctx.author.id in self.player_ids):
            # if [logic for checking picks]
            pass
    
    @commands.command()
    async def pack(self, ctx):
        if (isinstance(ctx.channel, discord.channel.DMChannel) and
                ctx.author.id in self.player_ids):
            await self.show_pack(ctx.author)

    async def show_pack(self, player):
        player_info = self.players[player.id]
        if not player_info.curr_pack:
            await player.send('You have no packs.')
        else:
            await player.send('Placeholder for how cards should look')

class DraftPlayer():
    def __init__(self, player, mtg_set):
        self.player = player
        self.mtg_set = mtg_set
        self.pack_q = asyncio.Queue()
        self.done = False

        self.left = None
        self.right = None

        self.curr_pack = None
        self.pack_msg = None

        self.reserved = []
        self.reserve_msg = None

        self.pool = []

    async def pack_runner(self):
        while not self.done:
            if not self.curr_pack:
                new_pack = await self.pack_q.get()
                self.curr_pack = new_pack
            await asyncio.sleep(5)

    def set_neighbors(self, left, right):
        self.left = left
        self.right = right

    def reserve(self, card_no):
        max_reserve = 1
        if self.mtg_set in ('CMR', 'BBD', '2XM'):
            max_reserve = 2
        
        if len(self.reserved) >= max_reserve:
            self.reserved.pop(0)
        self.reserved.append(self.curr_pack.cards[card_no-1])

        card_names = []
        for card in self.reserved:
            card_names.append(card['name'])

    def pick(self, card_no):
